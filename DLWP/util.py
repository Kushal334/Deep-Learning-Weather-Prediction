#
# Copyright (c) 2017-18 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
DLWP utilities.
"""

import pickle
import random
import tempfile
from importlib import import_module
from copy import copy
import numpy as np
import pandas as pd
import keras.models
from keras.utils import multi_gpu_model


# ==================================================================================================================== #
# General utility functions
# ==================================================================================================================== #


def make_keras_picklable():
    """
    Thanks to http://zachmoshe.com/2017/04/03/pickling-keras-models.html
    """

    def __getstate__(self):
        model_str = ""
        with tempfile.NamedTemporaryFile(suffix='.hdf5', delete=True) as fd:
            keras.models.save_model(self, fd.name, overwrite=True)
            model_str = fd.read()
        d = {'model_str': model_str}
        return d

    def __setstate__(self, state):
        with tempfile.NamedTemporaryFile(suffix='.hdf5', delete=True) as fd:
            fd.write(state['model_str'])
            fd.flush()
            model = keras.models.load_model(fd.name)
        self.__dict__ = model.__dict__

    cls = keras.models.Model
    cls.__getstate__ = __getstate__
    cls.__setstate__ = __setstate__


def get_object(module_class):
    """
    Given a string with a module class name, it imports and returns the class.
    This function (c) Tom Keffer, weeWX; modified by Jonathan Weyn.
    """
    # Split the path into its parts
    parts = module_class.split('.')
    # Get the top level module
    module = parts[0]  # '.'.join(parts[:-1])
    # Import the top level module
    mod = __import__(module)
    # Recursively work down from the top level module to the class name.
    # Be prepared to catch an exception if something cannot be found.
    try:
        for part in parts[1:]:
            module = '.'.join([module, part])
            # Import each successive module
            __import__(module)
            mod = getattr(mod, part)
    except ImportError as e:
        # Can't find a recursive module. Give a more informative error message:
        raise ImportError("'%s' raised when searching for %s" % (str(e), module))
    except AttributeError:
        # Can't find the last attribute. Give a more informative error message:
        raise AttributeError("Module '%s' has no attribute '%s' when searching for '%s'" %
                             (mod.__name__, part, module_class))

    return mod


def get_from_class(module_name, class_name):
    """
    Given a module name and a class name, return an object corresponding to the class retrieved as in
    `from module_class import class_name`.

    :param module_name: str: name of module (may have . attributes)
    :param class_name: str: name of class
    :return: object pointer to class
    """
    mod = __import__(module_name, fromlist=[class_name])
    class_obj = getattr(mod, class_name)
    return class_obj


def get_classes(module_name):
    """
    From a given module name, return a dictionary {class_name: class_object} of its classes.

    :param module_name: str: name of module to import
    :return: dict: {class_name: class_object} pairs in the module
    """
    module = import_module(module_name)
    classes = {}
    for key in dir(module):
        if isinstance(getattr(module, key), type):
            classes[key] = get_from_class(module_name, key)
    return classes


def get_methods(module_name):
    """
    From a given module name, return a dictionary {method_name: method_object} of its methods.

    :param module_name: str: name of module to import
    :return: dict: {method_name: method_object} pairs in the module
    """
    module = import_module(module_name)
    methods = {}
    for key in dir(module):
        if callable(getattr(module, key)):
            methods[key] = get_from_class(module_name, key)
    return methods


def save_model(model, file_name, history=None):
    """
    Saves a class instance with a 'model' attribute to disk. Creates two files: one pickle file containing no model
    saved as ${file_name}.pkl and one for the model saved as ${file_name}.keras. Use the `load_model()` method to load
    a model saved with this method.

    :param model: model instance (with a 'model' attribute) to save
    :param file_name: str: base name of save files
    :param history: history from Keras fitting, or None
    :return:
    """
    # Save the model structure and weights
    if hasattr(model, 'base_model'):
        model.base_model.save('%s.keras' % file_name)
    else:
        model.model.save('%s.keras' % file_name)
    # Create a picklable copy of the DLWP model object excluding the keras model
    model_copy = copy(model)
    model_copy.model = None
    if hasattr(model, 'base_model'):
        model_copy.base_model = None
    # Save the pickled DLWP object
    with open('%s.pkl' % file_name, 'wb') as f:
        pickle.dump(model_copy, f, protocol=pickle.HIGHEST_PROTOCOL)
    # Save the history, if requested
    if history is not None:
        with open('%s.history' % file_name, 'wb') as f:
            pickle.dump(history.history, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_model(file_name, history=False, custom_objects=None, gpus=1):
    """
    Loads a model saved to disk with the `save_model()` method.

    :param file_name: str: base name of save files
    :param history: bool: if True, loads the history file along with the model
    :param custom_objects: dict: any custom functions or classes to be included when Keras loads the model. There is
        no need to add objects in DLWP.custom as those are added automatically.
    :param gpus: int: load the model onto this number of GPUs
    :return: model [, dict]: loaded object [, dictionary of training history]
    """
    # Load the pickled DLWP object
    with open('%s.pkl' % file_name, 'rb') as f:
        model = pickle.load(f)
    # Load the saved keras model weights
    custom_objects = custom_objects or {}
    custom_objects.update(get_classes('DLWP.custom'))
    custom_objects.update(get_methods('DLWP.custom'))
    loaded_model = keras.models.load_model('%s.keras' % file_name, custom_objects=custom_objects, compile=True)
    # If multiple GPUs are requested, copy the model to the GPUs
    if gpus > 1:
        import tensorflow as tf
        with tf.device('/cpu:0'):
            model.base_model = keras.models.clone_model(loaded_model)
            model.base_model.set_weights(loaded_model.get_weights())
        model.model = multi_gpu_model(model.base_model, gpus=gpus)
        model.gpus = gpus
    else:
        model.base_model = loaded_model
        model.model = model.base_model
    # Also load the history file, if requested
    if history:
        with open('%s.history' % file_name, 'rb') as f:
            h = pickle.load(f)
        return model, h
    else:
        return model


def save_torch_model(model, file_name, history=None):
    """
    Saves a DLWPTorchNN model to disk. Creates two files: one pickle file containing the DLWPTorchNN wrapper, saved as
    ${file_name}.pkl, and one for the model saved as ${file_name}.torch. Use the `load_torch_model()` method to load
    a model saved with this method.

    :param model: DLWPTorchNN or other torch model to save
    :param file_name: str: base name of save files
    :param history: history of model to save; optional
    :return:
    """
    import torch
    torch.save(model.model, '%s.torch' % file_name)
    model_copy = copy(model)
    model_copy.model = None
    with open('%s.pkl' % file_name, 'wb') as f:
        pickle.dump(model_copy, f, protocol=pickle.HIGHEST_PROTOCOL)
    if history is not None:
        with open('%s.history' % file_name, 'wb') as f:
            pickle.dump(history, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_torch_model(file_name, history=False):
    """
    Loads a DLWPTorchNN or other model using Torch saved to disk with the `save_torch_model()` method.

    :param file_name: str: base name of save files
    :param history: bool: if True, loads the history file along with the model
    :return: model [, dict]: loaded object [, dictionary of training history]\
    """
    import torch
    with open('%s.pkl' % file_name, 'rb') as f:
        model = pickle.load(f)
    model.model = torch.load('%s.torch' % file_name)
    model.model.eval()
    if history:
        with open('%s.history' % file_name, 'rb') as f:
            h = pickle.load(f)
        return model, h
    else:
        return model


def delete_nan_samples(predictors, targets, large_fill_value=False, threshold=None):
    """
    Delete any samples from the predictor and target numpy arrays and return new, reduced versions.

    :param predictors: ndarray, shape [num_samples,...]: predictor data
    :param targets: ndarray, shape [num_samples,...]: target data
    :param large_fill_value: bool: if True, treats very large values (>= 1e20) as NaNs
    :param threshold: float 0-1: if not None, then removes any samples with a fraction of NaN larger than this
    :return: predictors, targets: ndarrays with samples removed
    """
    if threshold is not None and not (0 <= threshold <= 1):
        raise ValueError("'threshold' must be between 0 and 1")
    if large_fill_value:
        predictors[(predictors >= 1.e20) | (predictors <= -1.e20)] = np.nan
        targets[(targets >= 1.e20) | (targets <= -1.e20)] = np.nan
    p_shape = predictors.shape
    t_shape = targets.shape
    predictors = predictors.reshape((p_shape[0], -1))
    targets = targets.reshape((t_shape[0], -1))
    if threshold is None:
        p_ind = list(np.where(np.isnan(predictors))[0])
        t_ind = list(np.where(np.isnan(targets))[0])
    else:
        p_ind = list(np.where(np.mean(np.isnan(predictors), axis=1) >= threshold)[0])
        t_ind = list(np.where(np.mean(np.isnan(targets), axis=1) >= threshold)[0])
    bad_ind = list(set(p_ind + t_ind))
    predictors = np.delete(predictors, bad_ind, axis=0)
    targets = np.delete(targets, bad_ind, axis=0)
    new_p_shape = (predictors.shape[0],) + p_shape[1:]
    new_t_shape = (targets.shape[0],) + t_shape[1:]
    return predictors.reshape(new_p_shape), targets.reshape(new_t_shape)


def train_test_split_ind(n_sample, test_size, method='random'):
    """
    Return indices splitting n_samples into train and test index lists.

    :param n_sample: int: number of samples
    :param test_size: int: number of samples in test set
    :param method: str: 'first' ('last') to take first (last) t samples as test, or 'random'
    :return: (list, list): list of train indices, list of test indices
    """
    if method == 'first':
        test_set = list(range(0, test_size))
        train_set = list(range(test_size, n_sample))
    elif method == 'last':
        test_set = list(range(n_sample - test_size, n_sample))
        train_set = list(range(0, n_sample - test_size))
    elif method == 'random':
        train_set = list(range(n_sample))
        test_set = []
        for j in range(test_size):
            i = random.choice(train_set)
            test_set.append(i)
            train_set.remove(i)
        test_set.sort()
    else:
        raise ValueError("'method' must be 'first', 'last', or 'random'")

    return train_set, test_set


def day_of_year(date):
    year_start = pd.Timestamp(date.year, 1, 1)
    return (date - year_start).total_seconds() / 3600. / 24.


def insolation(dates, lat, lon, S=1.):
    """
    Calculate the approximate solar insolation for given dates

    :param dates: 1d array: datetime or Timestamp
    :param lat: 1d or 2d array of latitudes
    :param lon: 1d or 2d array of longitudes (0-360º). If 2d, must match the shape of lat.
    :param S: float: scaling factor (solar constant)
    :return: 3d array: insolation (date, lat, lon)
    """
    try:
        assert len(lat.shape) == len(lon.shape)
    except AssertionError:
        raise ValueError("'lat' and 'lon' must either both be 1d or both be 2d'")
    if len(lat.shape) == 2:
        try:
            assert lat.shape == lon.shape
        except AssertionError:
            raise ValueError("shape mismatch between lat (%s) and lon (%s)" % (lat.shape, lon.shape))
    if len(lat.shape) == 1:
        lon, lat = np.meshgrid(lon, lat)

    # Constants for year 1995 (standard)
    eps = 23.4441 * np.pi / 180.
    ecc = 0.016715
    om = 282.7 * np.pi / 180.
    beta = np.sqrt(1 - ecc ** 2.)
    # Get the day of year. Ignore leap days.
    days = pd.Series(dates)
    days = days.apply(day_of_year)
    # Longitude of the earth relative to the orbit, 1st order approximation
    lambda_m0 = ecc * (1. + beta) * np.sin(om)
    lambda_m = lambda_m0 + 2. * np.pi * (days.values - 80.5) / 365.
    lambda_ = lambda_m + 2. * ecc * np.sin(lambda_m - om)
    # Solar declination
    dec = np.arcsin(np.sin(eps) * np.sin(lambda_))
    # Hour angle
    h = 2 * np.pi * (days.values[:, None, None] + lon / 360.)
    # Distance
    rho = (1. - ecc ** 2.) / (1. + ecc * np.cos(lambda_ - om))

    # Insolation
    lat *= np.pi / 180.
    sol = S * (np.sin(lat[None, ...]) * np.sin(dec[:, None, None]) -
               np.cos(lat[None, ...]) * np.cos(dec[:, None, None]) * np.cos(h)) * rho[:, None, None] ** -2.
    sol[sol < 0.] = 0.

    return sol.astype(np.float32)
