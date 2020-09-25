# Lint as: python3
# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The Euler sampling method for ito processes."""

import tensorflow.compat.v2 as tf
from tf_quant_finance.math import custom_loops
from tf_quant_finance.math import random_ops as random
from tf_quant_finance.models import utils


def sample(dim,
           drift_fn,
           volatility_fn,
           times,
           time_step,
           num_samples=1,
           initial_state=None,
           random_type=None,
           seed=None,
           swap_memory=True,
           skip=0,
           precompute_normal_draws=True,
           watch_params=None,
           dtype=None,
           name=None):
  """Returns a sample paths from the process using Euler method.

  For an Ito process,

  ```
    dX = a(t, X_t) dt + b(t, X_t) dW_t
  ```
  with given drift `a` and volatility `b` functions Euler method generates a
  sequence {X_n} as

  ```
  X_{n+1} = X_n + a(t_n, X_n) dt + b(t_n, X_n) (N(0, t_{n+1}) - N(0, t_n)),
  ```
  where `dt = t_{n+1} - t_n` and `N` is a sample from the Normal distribution.
  See [1] for details.

  #### References
  [1]: Wikipedia. Euler-Maruyama method:
  https://en.wikipedia.org/wiki/Euler-Maruyama_method

  Args:
    dim: Python int greater than or equal to 1. The dimension of the Ito
      Process.
    drift_fn: A Python callable to compute the drift of the process. The
      callable should accept two real `Tensor` arguments of the same dtype.
      The first argument is the scalar time t, the second argument is the
      value of Ito process X - tensor of shape `batch_shape + [dim]`.
      The result is value of drift a(t, X). The return value of the callable
      is a real `Tensor` of the same dtype as the input arguments and of shape
      `batch_shape + [dim]`.
    volatility_fn: A Python callable to compute the volatility of the process.
      The callable should accept two real `Tensor` arguments of the same dtype
      and shape `times_shape`. The first argument is the scalar time t, the
      second argument is the value of Ito process X - tensor of shape
      `batch_shape + [dim]`. The result is value of drift b(t, X). The return
      value of the callable is a real `Tensor` of the same dtype as the input
      arguments and of shape `batch_shape + [dim, dim]`.
    times: Rank 1 `Tensor` of increasing positive real values. The times at
      which the path points are to be evaluated.
    time_step: Scalar real `Tensor` - maximal distance between points
        in grid in Euler schema.
    num_samples: Positive scalar `int`. The number of paths to draw.
      Default value: 1.
    initial_state: `Tensor` of shape `[dim]`. The initial state of the
      process.
      Default value: None which maps to a zero initial state.
    random_type: Enum value of `RandomType`. The type of (quasi)-random
      number generator to use to generate the paths.
      Default value: None which maps to the standard pseudo-random numbers.
    seed: Seed for the random number generator. The seed is
      only relevant if `random_type` is one of
      `[STATELESS, PSEUDO, HALTON_RANDOMIZED, PSEUDO_ANTITHETIC,
        STATELESS_ANTITHETIC]`. For `PSEUDO`, `PSEUDO_ANTITHETIC` and
      `HALTON_RANDOMIZED` the seed should be a Python integer. For
      `STATELESS` and  `STATELESS_ANTITHETIC `must be supplied as an integer
      `Tensor` of shape `[2]`.
      Default value: `None` which means no seed is set.
    swap_memory: A Python bool. Whether GPU-CPU memory swap is enabled for this
      op. See an equivalent flag in `tf.while_loop` documentation for more
      details. Useful when computing a gradient of the op since `tf.while_loop`
      is used to propagate stochastic process in time.
      Default value: True.
    skip: `int32` 0-d `Tensor`. The number of initial points of the Sobol or
      Halton sequence to skip. Used only when `random_type` is 'SOBOL',
      'HALTON', or 'HALTON_RANDOMIZED', otherwise ignored.
      Default value: `0`.
    precompute_normal_draws: Python bool. Indicates whether the noise increments
      `N(0, t_{n+1}) - N(0, t_n)` are precomputed. For `HALTON` and `SOBOL`
      random types the increments are always precomputed. While the resulting
      graph consumes more memory, the performance gains might be significant.
      Default value: `True`.
    watch_params: An optional list of zero-dimensional `Tensor`s of the same
      `dtype` as `initial_state`. If provided, specifies `Tensor`s with respect
      to which the differentiation of the sampling function will happen.
      A more efficient algorithm is used when `watch_params` are specified.
      Note the the function becomes differentiable onlhy wrt to these `Tensor`s
      and the `initial_state`. The gradient wrt any other `Tensor` is set to be
      zero.
    dtype: `tf.Dtype`. If supplied the dtype for the input and output `Tensor`s.
      Default value: None which means that the dtype implied by `times` is
      used.
    name: Python string. The name to give this op.
      Default value: `None` which maps to `euler_sample`.

  Returns:
   A real `Tensor` of shape [num_samples, k, n] where `k` is the size of the
      `times`, `n` is the dimension of the process.

  Raises:
    ValueError: If `time_step` or `times` have a non-constant value (e.g.,
      values are random), and `random_type` is `SOBOL`. This will be fixed with
      the release of TensorFlow 2.2.
  """
  name = name or 'euler_sample'
  with tf.name_scope(name):
    times = tf.convert_to_tensor(times, dtype=dtype)
    if dtype is None:
      dtype = times.dtype
    if initial_state is None:
      initial_state = tf.zeros(dim, dtype=dtype)
    initial_state = tf.convert_to_tensor(initial_state, dtype=dtype,
                                         name='initial_state')
    num_requested_times = times.shape.as_list()[0]
    # Create a time grid for the Euler scheme.
    times, keep_mask, time_indices = _prepare_grid(
        times=times, time_step=time_step, dtype=dtype)
    if watch_params is not None:
      watch_params = [tf.convert_to_tensor(param, dtype=dtype)
                      for param in watch_params]
    return _sample(
        dim=dim,
        drift_fn=drift_fn,
        volatility_fn=volatility_fn,
        times=times,
        time_step=time_step,
        keep_mask=keep_mask,
        num_requested_times=num_requested_times,
        num_samples=num_samples,
        initial_state=initial_state,
        random_type=random_type,
        seed=seed,
        swap_memory=swap_memory,
        skip=skip,
        precompute_normal_draws=precompute_normal_draws,
        watch_params=watch_params,
        time_indices=time_indices,
        dtype=dtype)


def _sample(*, dim, drift_fn, volatility_fn, times, time_step, keep_mask,
            num_requested_times,
            num_samples, initial_state, random_type,
            seed, swap_memory, skip, precompute_normal_draws,
            watch_params,
            time_indices,
            dtype):
  """Returns a sample of paths from the process using Euler method."""
  dt = times[1:] - times[:-1]
  # TODO(b/169400743): nn.relu can be removed when the bug is fixed.
  sqrt_dt = tf.sqrt(tf.nn.relu(dt))
  current_state = initial_state + tf.zeros([num_samples, dim],
                                           dtype=initial_state.dtype)
  if dt.shape.is_fully_defined():
    steps_num = dt.shape.as_list()[-1]
  else:
    steps_num = tf.shape(dt)[-1]
  # In order to use low-discrepancy random_type we need to generate the sequence
  # of independent random normals upfront. We also precompute random numbers
  # for stateless random type in order to ensure independent samples for
  # multiple function calls whith different seeds.
  if precompute_normal_draws or random_type in (
      random.RandomType.SOBOL,
      random.RandomType.HALTON,
      random.RandomType.HALTON_RANDOMIZED,
      random.RandomType.STATELESS,
      random.RandomType.STATELESS_ANTITHETIC):
    normal_draws = utils.generate_mc_normal_draws(
        num_normal_draws=dim, num_time_steps=steps_num,
        num_sample_paths=num_samples, random_type=random_type,
        dtype=dtype, seed=seed, skip=skip)
    wiener_mean = None
  else:
    # If pseudo or anthithetic sampling is used, proceed with random sampling
    # at each step.
    wiener_mean = tf.zeros((dim,), dtype=dtype, name='wiener_mean')
    normal_draws = None
  if watch_params is None:
    # Use while_loop if `watch_params` is not passed
    return  _while_loop(
        dim=dim, steps_num=steps_num,
        current_state=current_state,
        drift_fn=drift_fn, volatility_fn=volatility_fn, wiener_mean=wiener_mean,
        num_samples=num_samples, times=times,
        dt=dt, sqrt_dt=sqrt_dt, time_step=time_step, keep_mask=keep_mask,
        num_requested_times=num_requested_times,
        swap_memory=swap_memory,
        random_type=random_type, seed=seed, normal_draws=normal_draws)
  else:
    # Use custom for_loop if `watch_params` is specified
    return _for_loop(
        steps_num=steps_num, current_state=current_state,
        drift_fn=drift_fn, volatility_fn=volatility_fn, wiener_mean=wiener_mean,
        num_samples=num_samples, times=times,
        dt=dt, sqrt_dt=sqrt_dt, time_indices=time_indices,
        keep_mask=keep_mask, watch_params=watch_params,
        random_type=random_type, seed=seed, normal_draws=normal_draws)


def _while_loop(*, dim, steps_num, current_state,
                drift_fn, volatility_fn, wiener_mean,
                num_samples, times, dt, sqrt_dt, time_step, num_requested_times,
                keep_mask, swap_memory, random_type, seed, normal_draws):
  """Smaple paths using tf.while_loop."""
  cond_fn = lambda i, *args: i < steps_num
  def step_fn(i, written_count, current_state, result):
    return _euler_step(
        i=i,
        written_count=written_count,
        current_state=current_state,
        result=result,
        drift_fn=drift_fn,
        volatility_fn=volatility_fn,
        wiener_mean=wiener_mean,
        num_samples=num_samples,
        times=times,
        dt=dt,
        sqrt_dt=sqrt_dt,
        keep_mask=keep_mask,
        random_type=random_type,
        seed=seed,
        normal_draws=normal_draws)
  maximum_iterations = (tf.cast(1. / time_step, dtype=tf.int32)
                        + tf.size(times))
  # Include initial state, if necessary
  result = tf.zeros((num_samples, num_requested_times, dim),
                    dtype=current_state.dtype)
  result = utils.maybe_update_along_axis(
      tensor=result,
      do_update=keep_mask[0],
      ind=0,
      axis=1,
      new_tensor=tf.expand_dims(current_state, axis=1))
  written_count = tf.cast(keep_mask[0], dtype=tf.int32)
  # Sample paths
  _, _, _, result = tf.while_loop(
      cond_fn, step_fn, (0, written_count, current_state, result),
      maximum_iterations=maximum_iterations,
      swap_memory=swap_memory)
  return result


def _for_loop(*, steps_num, current_state,
              drift_fn, volatility_fn, wiener_mean, watch_params,
              num_samples, times, dt, sqrt_dt, time_indices,
              keep_mask, random_type, seed, normal_draws):
  """Smaple paths using custom for_loop."""
  num_time_points = time_indices.shape.as_list()[-1]
  if num_time_points == 1:
    iter_nums = steps_num
  else:
    iter_nums = time_indices
  def step_fn(i, current_state):
    # Unpack current_state
    current_state = current_state[0]
    _, _, next_state, _ = _euler_step(
        i=i,
        written_count=0,
        current_state=current_state,
        result=tf.expand_dims(current_state, axis=1),
        drift_fn=drift_fn,
        volatility_fn=volatility_fn,
        wiener_mean=wiener_mean,
        num_samples=num_samples,
        times=times,
        dt=dt,
        sqrt_dt=sqrt_dt,
        keep_mask=keep_mask,
        random_type=random_type,
        seed=seed,
        normal_draws=normal_draws)
    return [next_state]
  result = custom_loops.for_loop(
      body_fn=step_fn,
      initial_state=[current_state],
      params=watch_params,
      num_iterations=iter_nums)[0]
  if num_time_points == 1:
    return tf.expand_dims(result, axis=1)
  return tf.transpose(result, (1, 0, 2))


def _euler_step(*, i, written_count, current_state, result,
                drift_fn, volatility_fn, wiener_mean,
                num_samples, times, dt, sqrt_dt, keep_mask,
                random_type, seed, normal_draws):
  """Performs one step of Euler scheme."""
  current_time = times[i + 1]
  written_count = tf.cast(written_count, tf.int32)
  if normal_draws is not None:
    dw = normal_draws[i]
  else:
    dw = random.mv_normal_sample(
        (num_samples,), mean=wiener_mean, random_type=random_type,
        seed=seed)
  dw = dw * sqrt_dt[i]
  dt_inc = dt[i] * drift_fn(current_time, current_state)  # pylint: disable=not-callable
  dw_inc = tf.linalg.matvec(volatility_fn(current_time, current_state), dw)  # pylint: disable=not-callable
  next_state = current_state + dt_inc + dw_inc
  result = utils.maybe_update_along_axis(
      tensor=result,
      do_update=keep_mask[i + 1],
      ind=written_count,
      axis=1,
      new_tensor=tf.expand_dims(next_state, axis=1))
  written_count += tf.cast(keep_mask[i + 1], dtype=tf.int32)
  return i + 1, written_count, next_state, result


def _prepare_grid(*, times, time_step, dtype):
  """Prepares grid of times for path generation.

  Args:
    times:  Rank 1 `Tensor` of increasing positive real values. The times at
      which the path points are to be evaluated.
    time_step: Rank 0 real `Tensor`. Maximal distance between points in
      resulting grid.
    dtype: `tf.Dtype` of the input and output `Tensor`s.

  Returns:
    Tuple `(all_times, mask, time_points)`.
    `all_times` is a 1-D real `Tensor` containing all points from 'times` and
    the uniform grid of points between `[0, times[-1]]` with grid size equal to
    `time_step`. The `Tensor` is sorted in ascending order and may contain
    duplicates.
    `mask` is a boolean 1-D `Tensor` of the same shape as 'all_times', showing
    which elements of 'all_times' correspond to THE values from `times`.
    Guarantees that times[0]=0 and mask[0]=False.
    `time_indices`. An integer `Tensor` of the same shape as `times` indicating
    `times` indices in `all_times`.
  """
  grid = tf.range(0.0, times[-1], time_step, dtype=dtype)
  all_times = tf.concat([times, grid], axis=0)
  # Remove duplicate points
  all_times = tf.unique(all_times).y
  # Sort sequence. Identify the time indices of interest
  # TODO(b/169400743): use tf.sort instead of argsort and casting when XLA
  # float64 support is extended for tf.sort
  args = tf.argsort(tf.cast(all_times, dtype=tf.float32))
  all_times = tf.gather(all_times, args)
  time_indices = tf.searchsorted(all_times, times, out_type=tf.int32)
  # Create a boolean mask to identify the iterations that have to be recorded.
  mask_sparse = tf.sparse.SparseTensor(
      indices=tf.expand_dims(
          tf.cast(time_indices, dtype=tf.int64), axis=1),
      values=tf.fill(times.shape, True),
      dense_shape=all_times.shape)
  mask = tf.sparse.to_dense(mask_sparse)
  return all_times, mask, time_indices


__all__ = ['sample']

