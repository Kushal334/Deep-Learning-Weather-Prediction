#
# Copyright (c) 2019 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
Methods for validating DLWP forecasts.
"""

import numpy as np
import pandas as pd
import xarray as xr
from datetime import timedelta


def forecast_error(forecast, valid, method='mse', axis=None):
    """
    Calculate the error of a time series model forecast.

    :param forecast: ndarray: forecast from a DLWP model (forecast hour is first axis)
    :param valid: ndarray: validation target data for the predictors the forecast was made on
    :param method: str: 'mse' for mean squared error, 'mae' for mean absolute error, 'rmse' for root-mean-square
    :param axis: int, tuple, or None: take the mean of the error along this axis. Regardless of this setting, the
        forecast hour will be the first dimension.
    :return: ndarray: forecast error with forecast hour as the first dimension
    """
    if method not in ['mse', 'mae', 'rmse']:
        raise ValueError("'method' must be 'mse', 'rmse', or 'mae'")
    n_f = forecast.shape[0]
    if len(forecast.shape) == len(valid.shape):
        # valid must include a forecast hour dimension
        if axis is None:
            axis = tuple(range(1, len(valid.shape)))
        if method == 'mse':
            return np.nanmean((valid - forecast) ** 2., axis=axis)
        elif method == 'mae':
            return np.nanmean(np.abs(valid - forecast), axis=axis)
        elif method == 'rmse':
            return np.sqrt(np.nanmean((valid - forecast) ** 2., axis=axis))
    else:
        n_val = valid.shape[0]
        me = []
        for f in range(n_f):
            if method == 'mse':
                me.append(np.nanmean((valid[f:] - forecast[f, :(n_val - f)]) ** 2., axis=axis))
            elif method == 'mae':
                me.append(np.nanmean(np.abs(valid[f:] - forecast[f, :(n_val - f)]), axis=axis))
            elif method == 'rmse':
                me.append(np.sqrt(np.nanmean((valid[f:] - forecast[f, :(n_val - f)]) ** 2., axis=axis)))
        return np.array(me)


def persistence_error(predictors, valid, n_fhour, method='mse', axis=None):
    """
    Calculate the error of a persistence forecast out to n_fhour forecast hours.

    :param predictors: ndarray: predictor data
    :param valid: ndarray: validation target data
    :param n_fhour: int: number of steps to take forecast out to
    :param method: str: 'mse' for mean squared error, 'mae' for mean absolute error, 'rmse' for root-mean-square
    :param axis: int, tuple, or None: take the mean of the error along this axis. Regardless of this setting, the
        forecast hour will be the first dimension.
    :return: ndarray: persistence error with forecast hour as the first dimension
    """
    if method not in ['mse', 'mae', 'rmse']:
        raise ValueError("'method' must be 'mse', 'rmse', or 'mae'")
    n_f = valid.shape[0]
    me = []
    for f in range(n_fhour):
        if method == 'mse':
            me.append(np.nanmean((valid[f:] - predictors[:(n_f - f)]) ** 2., axis=axis))
        elif method == 'mae':
            me.append(np.nanmean(np.abs(valid[f:] - predictors[:(n_f - f)]), axis=axis))
        elif method == 'rmse':
            me.append(np.sqrt(np.nanmean((valid[f:] - predictors[:(n_f - f)]) ** 2., axis=axis)))
    return np.array(me)


def climo_error(valid, n_fhour, method='mse', axis=None):
    """
    Calculate the error of a climatology forecast out to n_fhour forecast hours.

    :param valid: ndarray: validation target data
    :param n_fhour: int: number of steps to take forecast out to
    :param method: str: 'mse' for mean squared error, 'mae' for mean absolute error, 'rmse' for root-mean-square
    :param axis: int, tuple, or None: take the mean of the error along this axis. Regardless of this setting, the
        forecast hour will be the first dimension.
    :return: ndarray: persistence error with forecast hour as the first dimension
    """
    if method not in ['mse', 'mae', 'rmse']:
        raise ValueError("'method' must be 'mse', 'rmse', or 'mae'")
    n_f = valid.shape[0]
    me = []
    for f in range(n_fhour):
        if method == 'mse':
            me.append(np.nanmean((valid[:(n_f - f)] - np.nanmean(valid, axis=0)) ** 2., axis=axis))
        elif method == 'mae':
            me.append(np.nanmean(np.abs(valid[:(n_f - f)] - np.nanmean(valid, axis=0)), axis=axis))
        elif method == 'rmse':
            me.append(np.sqrt(np.nanmean((valid[:(n_f - f)] - np.nanmean(valid, axis=0)) ** 2., axis=axis)))
    return np.array(me)


def monthly_climo_error(da, val_set, n_fhour=None, method='mse', return_da=False):
    """
    Calculates a month-aware climatology error for a validation set from a DataArray of the atmospheric state.

    :param da: xarray DataArray: contains a 'time' or 'sample' dimension
    :param val_set: list: list of times for which to calculate an error
    :param n_fhour: int or None: if int, multiplies the resulting error into a list of length n_fhour
    :param method: str: 'mse' for mean squared error, 'mae' for mean absolute error, 'rmse' for root-mean-square
    :param return_da: bool: if True, also returns a DataArray of the error from climatology
    :return: (int or list[, DataArray])
    """
    if method not in ['mse', 'mae', 'rmse']:
        raise ValueError("'method' must be 'mse', 'rmse', or 'mae'")
    time_dim = 'sample' if 'sample' in da.dims else 'time'
    monthly_climo = da.groupby('%s.month' % time_dim).mean(time_dim)
    anomaly = da.sel(**{time_dim: val_set}).groupby('%s.month' % time_dim) - monthly_climo
    if method == 'mse':
        me = float((anomaly ** 2.).mean().values)
    elif method == 'mae':
        me = float(anomaly.abs().mean().values)
    elif method == 'rmse':
        me = np.sqrt(float((anomaly ** 2.).mean().values))
    if n_fhour is not None:
        me = np.array([me] * n_fhour)
    if return_da:
        return me, anomaly
    else:
        return me


def predictors_to_time_series(predictors, time_steps, has_time_dim=True, use_first_step=False, meta_ds=None):
    """
    Reshapes predictors into a continuous time series that can be used for verification methods in this module and
    matches the reshaped output of DLWP models' 'predict_timeseries' method. This is only necessary if the data are for
    a model predicting multiple time steps. Also truncates the first (time_steps - 1) samples so that the time series
    matches the effective forecast initialization time, or the last (time_steps -1) samples if use_first_step == True.

    :param predictors: ndarray: array of predictor data
    :param time_steps: int: number of time steps in the predictor data
    :param has_time_dim: bool: if True, the time step dimension is axis=1 in the predictors, otherwise, axis 1 is
        assumed to be time_steps * num_channels_or_features
    :param use_first_step: bool: if True, keeps the first time step instead of the last (useful for validation)
    :param meta_ds: xarray Dataset: if not None, add metadata to the output using the coordinates in this Dataset
    :return: ndarray or xarray DataArray: reshaped predictors
    """
    idx = 0 if use_first_step else -1
    if has_time_dim:
        result = predictors[:, idx]
    else:
        sample_dim = predictors.shape[0]
        feature_shape = predictors.shape[1:]
        predictors = predictors.reshape((sample_dim, time_steps, -1) + feature_shape[1:])
        result = predictors[:, idx]
    if meta_ds is not None:
        if 'level' in meta_ds.dims:
            result = result.reshape((meta_ds.dims['sample'], meta_ds.dims['variable'], meta_ds.dims['level'],
                                     meta_ds.dims['lat'], meta_ds.dims['lon']))
            result = xr.DataArray(result,
                                  coords=[meta_ds.sample, meta_ds.variable, meta_ds.level, meta_ds.lat, meta_ds.lon],
                                  dims=['time', 'variable', 'level', 'lat', 'lon'])
        else:
            result = xr.DataArray(result, coords=[meta_ds.sample, meta_ds.varlev, meta_ds.lat, meta_ds.lon],
                                  dims=['time', 'varlev', 'lat', 'lon'])

    return result


def add_metadata_to_forecast(forecast, f_hour, meta_ds):
    """
    Add metadata to a forecast based on the initialization times and coordinates in meta_ds.

    :param forecast: ndarray: (forecast_hour, time, variable, lat, lon)
    :param f_hour: iterable: forecast hour coordinate values
    :param meta_ds: xarray Dataset: contains metadata for time, variable, lat, and lon
    :return: xarray.DataArray: array with metadata
    """
    nf = len(f_hour)
    if nf != forecast.shape[0]:
        raise ValueError("'f_hour' coordinate must have same size as the first axis of 'forecast'")
    if 'level' in meta_ds.dims:
        forecast = forecast.reshape((nf, meta_ds.dims['sample'], meta_ds.dims['variable'], meta_ds.dims['level'],
                                     meta_ds.dims['lat'], meta_ds.dims['lon']))
        forecast = xr.DataArray(
            forecast,
            coords=[f_hour, meta_ds.sample, meta_ds.variable, meta_ds.level, meta_ds.lat, meta_ds.lon],
            dims=['f_hour', 'time', 'variable', 'level', 'lat', 'lon']
        )
    else:
        forecast = xr.DataArray(
            forecast,
            coords=[f_hour, meta_ds.sample, meta_ds.varlev, meta_ds.lat, meta_ds.lon],
            dims=['f_hour', 'time', 'varlev', 'lat', 'lon']
        )
    return forecast


def verification_from_samples(ds, all_ds=None, forecast_steps=1, dt=6):
    """
    Generate a DataArray of forecast verification from a validation DataSet built using Preprocessor.data_to_samples().

    :param ds: xarray.Dataset: dataset of verification data
    :param all_ds: xarray.Dataset: optional Dataset containing the same variables/levels/lat/lon as val_ds but
        including more time steps for more robust handling of data at times outside of the validation selection
    :param forecast_steps: int: number of forward forecast iterations
    :param dt: int: forecast time step in hours
    :return: xarray.DataArray: verification with forecast hour as the first dimension
    """
    forecast_steps = int(forecast_steps)
    if forecast_steps < 1:
        raise ValueError("'forecast_steps' must be an integer >= 1")
    dt = int(dt)
    if dt < 1:
        raise ValueError("'dt' must be an integer >= 1")
    dims = [d for d in ds.predictors.dims if d.lower() != 'time_step']
    f_hour = np.arange(dt, dt * forecast_steps + 1, dt).astype('timedelta64[h]')
    verification = xr.DataArray(
        np.full([forecast_steps] + [ds.dims[d] for d in dims], np.nan, dtype=np.float32),
        coords=[f_hour] + [ds[d] for d in dims],
        dims=['f_hour'] + dims
    )
    if all_ds is not None:
        valid_da = all_ds.targets.isel(time_step=0)
    else:
        valid_da = ds.targets.isel(time_step=0)
    for d, date in enumerate(ds.sample.values[:]):
        verification[:, d] = valid_da.reindex(
            sample=pd.date_range(date, date + np.timedelta64(timedelta(hours=dt * (forecast_steps - 1))),
                                 freq='%sH' % int(dt)),
            method=None
        ).values
    return verification.rename({'sample': 'time'})


def verification_from_series(ds, all_ds=None, forecast_steps=1, dt=6):
    """
    Generate a DataArray of forecast verification from a validation DataSet built using Preprocessor.data_to_series().

    :param ds: xarray.Dataset: dataset of verification data
    :param all_ds: xarray.Dataset: optional Dataset containing the same variables/levels/lat/lon as val_ds but
        including more time steps for more robust handling of data at times outside of the validation selection
    :param forecast_steps: int: number of forward forecast iterations
    :param dt: int: forecast time step in hours
    :return: xarray.DataArray: verification with forecast hour as the first dimension
    """
    forecast_steps = int(forecast_steps)
    if forecast_steps < 1:
        raise ValueError("'forecast_steps' must be an integer >= 1")
    dt = int(dt)
    if dt < 1:
        raise ValueError("'dt' must be an integer >= 1")
    dims = [d for d in ds.predictors.dims if d.lower() != 'time_step']
    f_hour = np.arange(dt, dt * forecast_steps + 1, dt).astype('timedelta64[h]')
    verification = xr.DataArray(
        np.full([forecast_steps] + [ds.dims[d] for d in dims], np.nan, dtype=np.float32),
        coords=[f_hour] + [ds[d] for d in dims],
        dims=['f_hour'] + dims
    )
    if all_ds is not None:
        valid_da = all_ds.predictors
    else:
        valid_da = ds.predictors
    for d, date in enumerate(ds.sample.values):
        verification[:, d] = valid_da.reindex(
            sample=pd.date_range(date + np.timedelta64(timedelta(hours=dt)),
                                 date + np.timedelta64(timedelta(hours=dt * forecast_steps)),
                                 freq='%sH' % int(dt)),
            method=None
        ).values
    return verification.rename({'sample': 'time'})
