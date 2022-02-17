#
# Copyright (c) 2019 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
Example of training a DLWP model using a dataset of predictors generated with DLWP.model.Preprocessor. This example
uses the old DataGenerator and should be considered deprecated in favor of the new example 'train.py' which takes
advantage of the advanced configuration capabilities of Preprocessor.data_to_series() and the SeriesDataGenerator.
"""

import time
import os
import numpy as np
import pandas as pd
import xarray as xr
from datetime import datetime
from DLWP.model import DLWPNeuralNet, DataGenerator, SmartDataGenerator
from DLWP.util import save_model, train_test_split_ind
from DLWP.custom import RNNResetStates, EarlyStoppingMin, latitude_weighted_loss
from keras.regularizers import l2
from keras.losses import mean_squared_error
from keras.callbacks import History, TensorBoard


#%% Open the predictor data

root_directory = '/home/disk/wave2/jweyn/Data/DLWP'
predictor_file = os.path.join(root_directory, 'cfs_6h_1979-2010_z500-th3-7-w700-rh850-pwat_NH_T2.nc')
model_file = os.path.join(root_directory, 'dlwp_6h_1979-2010_z500-th3-7-w700-rh850_NH_T2_400')
log_directory = os.path.join(root_directory, 'logs', 'pwat-w-rh-lstm')
model_is_convolutional = True
model_is_recurrent = True

# NN parameters. Regularization is applied to LSTM layers by default. weight_loss indicates whether to weight the
# loss function preferentially in the mid-latitudes.
min_epochs = 400
max_epochs = 1000
patience = 50
batch_size = 64
lambda_ = 1.e-4
weight_loss = False

data = xr.open_dataset(predictor_file)
data = data.chunk({'sample': batch_size})
if 'time_step' in data.dims:
    time_dim = data.dims['time_step']
else:
    time_dim = 1
n_sample = data.dims['sample']

# If system memory permits, loading the predictor data can greatly increase efficiency when training on GPUs, if the
# train computation takes less time than the data loading. The option load_continuous allows a further reduction of
# memory use by a factor of at least 2 if the train_set is a continuous time sequence of data.
load_memory = True
load_continuous = True

# Validation set to use. Either an integer (number of validation samples, taken from the end), or an iterable of
# pandas datetime objects. The train set can be set to the first <integer> samples, an iterable of dates, or None to
# simply use the remaining points. Match the type of validation_set.
validation_set = list(pd.date_range(datetime(2003, 1, 1, 6), datetime(2006, 12, 31, 6), freq='6H'))
train_set = list(pd.date_range(datetime(1979, 1, 1, 6), datetime(2003, 1, 1, 0), freq='6H'))

# For upsampling, we need an even number of lat/lon points. We'll crop out the north pole.
data = data.isel(lat=(data.lat < 90.0))

# Cut out some variables
data = data.sel(varlev=['V VEL/700', 'HGT/500', 'THICK/300-700', 'R H/850'])


#%% Build a model and the data generators

dlwp = DLWPNeuralNet(is_convolutional=model_is_convolutional, is_recurrent=model_is_recurrent, time_dim=time_dim,
                     scaler_type=None, scale_targets=False)

# Find the validation set
if isinstance(validation_set, int):
    n_sample = data.dims['sample']
    ts, val_set = train_test_split_ind(n_sample, validation_set, method='last')
    if train_set is None:
        train_set = ts
    elif isinstance(train_set, int):
        train_set = list(range(train_set))
    validation_data = data.isel(sample=val_set)
    train_data = data.isel(sample=train_set)
elif validation_set is None:
    if train_set is None:
        train_set = data.sample.values
    validation_data = None
    train_data = data.sel(sample=train_set)
else:  # we must have a list of datetimes
    if train_set is None:
        train_set = np.isin(data.sample.values, np.array(validation_set, dtype='datetime64[ns]'),
                            assume_unique=True, invert=True)
    validation_data = data.sel(sample=validation_set)
    train_data = data.sel(sample=train_set)

# Build the data generators
if load_continuous:
    if load_memory:
        print('Loading data to memory...')
    generator = SmartDataGenerator(dlwp, train_data, batch_size=batch_size, load=load_memory)
else:
    generator = DataGenerator(dlwp, train_data, batch_size=batch_size)
if validation_data is not None:
    val_generator = DataGenerator(dlwp, validation_data, batch_size=batch_size)


#%% Compile the model structure with some generator data information

# # Convolutional LSTM
# layers = (
#     ('Reshape', (generator.shape_2d,), {'input_shape': generator.convolution_shape}),
#     ('PeriodicPadding2D', ((0, 2),), {'data_format': 'channels_first'}),
#     ('ZeroPadding2D', ((2, 0),), {'data_format': 'channels_first'}),
#     ('Reshape', ((2, 1, 41, 148),), None),
#     ('ConvLSTM2D', (16, 5), {
#         'strides': 1,
#         'padding': 'valid',
#         'data_format': 'channels_first',
#         'activation': 'tanh',
#         'kernel_regularizer': l2(lambda_),
#         'return_sequences': True,
#         'input_shape': generator.convolution_shape
#         # 'stateful': True,
#         # 'batch_input_shape': (batch_size, 1,) + generator.convolution_shape,
#     }),
#     ('Reshape', ((2 * 16, 37, 144),), None),
#     ('PeriodicPadding2D', ((0, 2),), {'data_format': 'channels_first'}),
#     ('ZeroPadding2D', ((2, 0),), {'data_format': 'channels_first'}),
#     ('Conv2D', (16, 5), {
#         'strides': 2,
#         'padding': 'valid',
#         'data_format': 'channels_first',
#         'activation': 'tanh',
#     }),
#     ('Flatten', None, None),
#     ('Dense', (generator.n_features,), {'activation': 'linear'}),
#     ('Reshape', (generator.convolution_shape,), None)
# )

# Up-sampling convolutional network with LSTM layer
cs = generator.convolution_shape
layers = (
    # --- These layers add a convolutional LSTM at the beginning --- #
    ('Reshape', (generator.shape_2d,), {'input_shape': cs}),
    ('PeriodicPadding2D', ((0, 2),), {'data_format': 'channels_first'}),
    ('ZeroPadding2D', ((2, 0),), {'data_format': 'channels_first'}),
    ('Reshape', ((cs[0], cs[1], cs[2] + 4, cs[3] + 4),), None),
    ('ConvLSTM2D', (4 * cs[1], 3), {
        'dilation_rate': 2,
        'padding': 'valid',
        'data_format': 'channels_first',
        'activation': 'tanh',
        'return_sequences': True,
        'kernel_regularizer': l2(lambda_)
    }),
    ('Reshape', ((4 * cs[0] * cs[1], cs[2], cs[3]),), None),
    # -------------------------------------------------------------- #
    ('PeriodicPadding2D', ((0, 2),), {
        'data_format': 'channels_first',
        'input_shape': cs
    }),
    ('ZeroPadding2D', ((2, 0),), {'data_format': 'channels_first'}),
    ('Conv2D', (32, 3), {
        'dilation_rate': 2,
        'padding': 'valid',
        'activation': 'tanh',
        'data_format': 'channels_first'
    }),
    # ('BatchNormalization', None, {'axis': 1}),
    ('MaxPooling2D', (2,), {'data_format': 'channels_first'}),
    ('PeriodicPadding2D', ((0, 1),), {'data_format': 'channels_first'}),
    ('ZeroPadding2D', ((1, 0),), {'data_format': 'channels_first'}),
    ('Conv2D', (64, 3), {
        'dilation_rate': 1,
        'padding': 'valid',
        'activation': 'tanh',
        'data_format': 'channels_first'
    }),
    # ('BatchNormalization', None, {'axis': 1}),
    ('MaxPooling2D', (2,), {'data_format': 'channels_first'}),
    ('PeriodicPadding2D', ((0, 1),), {'data_format': 'channels_first'}),
    ('ZeroPadding2D', ((1, 0),), {'data_format': 'channels_first'}),
    ('Conv2D', (128, 3), {
        'dilation_rate': 1,
        'padding': 'valid',
        'activation': 'tanh',
        'data_format': 'channels_first'
    }),
    # ('BatchNormalization', None, {'axis': 1}),
    ('UpSampling2D', (2,), {'data_format': 'channels_first'}),
    ('PeriodicPadding2D', ((0, 1),), {'data_format': 'channels_first'}),
    ('ZeroPadding2D', ((1, 0),), {'data_format': 'channels_first'}),
    ('Conv2D', (64, 3), {
        'dilation_rate': 1,
        'padding': 'valid',
        'activation': 'tanh',
        'data_format': 'channels_first'
    }),
    # ('BatchNormalization', None, {'axis': 1}),
    ('UpSampling2D', (2,), {'data_format': 'channels_first'}),
    ('PeriodicPadding2D', ((0, 2),), {'data_format': 'channels_first'}),
    ('ZeroPadding2D', ((2, 0),), {'data_format': 'channels_first'}),
    ('Conv2D', (32, 3), {
        'dilation_rate': 2,
        'padding': 'valid',
        'activation': 'tanh',
        'data_format': 'channels_first'
    }),
    # ('BatchNormalization', None, {'axis': 1}),
    ('PeriodicPadding2D', ((0, 2),), {'data_format': 'channels_first'}),
    ('ZeroPadding2D', ((2, 0),), {'data_format': 'channels_first'}),
    # --- Change the number of filters to cs[0] * cs[1] for LSTM model --- #
    ('Conv2D', (cs[0] * cs[1], 5), {  # cs[0] * cs[1]
        'padding': 'valid',
        'activation': 'linear',
        'data_format': 'channels_first'
    }),
    ('Reshape', (generator.convolution_shape,), None)
)

# # Feed-forward dense neural network
# layers = (
#     ('Dense', (generator.n_features // 2,), {
#         'activation': 'tanh',
#         'kernel_regularizer': l2(lambda_),
#         'input_shape': (generator.n_features,)
#     }),
#     ('Dropout', (0.25,), None),
#     ('Dense', (generator.n_features,), {'activation': 'linear'})
# )

# Example custom loss function: pass to loss= in build_model()
if weight_loss:
    loss_function = latitude_weighted_loss(mean_squared_error, generator.ds.lat.values, generator.convolution_shape,
                                           axis=-2, weighting='midlatitude')
else:
    loss_function = 'mse'

# Build the model
dlwp.build_model(layers, loss=loss_function, optimizer='adam', metrics=['mae'], gpus=2)
print(dlwp.model.summary())


#%% Load the scaling and validating data

# Generate the data to fit the scaler and imputer. The generator will by default apply scaling because it is necessary
# to automate its use in the Keras fit_generator method, so disable it when dealing with data to fit the scaler and
# imputer. If using pre-scaled data (i.e., dlwp was initialized with scaler_type=None and the Preprocessor data was
# generated with scale_variables=True), this step should be omitted.
fit_set = list(range(2))  # Use a much larger value when it matters
p_fit, t_fit = generator.generate(fit_set, scale_and_impute=False)
dlwp.init_fit(p_fit, t_fit)
p_fit, t_fit = (None, None)

if load_memory and not load_continuous:
    print('Loading data to memory...')
    p_train, t_train = generator.generate([], scale_and_impute=False)

# Load the validation data. Better to load in memory to avoid file read errors while training.
if validation_data is None:
    val = None
else:
    print('Loading validation data to memory...')
    val = val_generator.generate([], scale_and_impute=False)


#%% Train, evaluate, and save the model

# Train and evaluate the model
start_time = time.time()
print('Begin training...')
history = History()
early = EarlyStoppingMin(min_epochs=min_epochs, monitor='val_loss', min_delta=0., patience=patience,
                         restore_best_weights=True, verbose=1)
tensorboard = TensorBoard(log_dir=log_directory, batch_size=batch_size, update_freq='epoch')
if load_memory and not load_continuous:
    dlwp.fit(p_train, t_train, batch_size=batch_size, epochs=max_epochs, verbose=1, validation_data=val,
             callbacks=[history, RNNResetStates(), early, tensorboard])
else:
    dlwp.fit_generator(generator, epochs=max_epochs, verbose=1, validation_data=val, use_multiprocessing=True,
                       callbacks=[history, RNNResetStates(), early, tensorboard])
end_time = time.time()

# Evaluate the model
print("\nTrain time -- %s seconds --" % (end_time - start_time))
if validation_data is not None:
    score = dlwp.evaluate(*val, verbose=0)
    print('Validation loss:', score[0])
    print('Validation mean absolute error:', score[1])

# Save the model
if model_file is not None:
    save_model(dlwp, model_file, history=history)
