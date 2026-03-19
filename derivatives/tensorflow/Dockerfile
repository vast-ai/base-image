ARG VAST_BASE=vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04

FROM ${VAST_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Tensorflow image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG TENSORFLOW_VERSION
ARG VAST_BASE

RUN \
    set -euo pipefail && \
    [[ -n "${TENSORFLOW_VERSION}" ]] || { echo "Must specify TENSORFLOW_VERSION" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We will need to modify this if we want to support ROCm
    pip install --no-cache-dir \
        tensorflow[and-cuda]==${TENSORFLOW_VERSION}  && \
   # Test 1: Verify TensorFlow version
   python3 -c "import tensorflow as tf; assert tf.__version__.startswith('${TENSORFLOW_VERSION}'), f'Version mismatch: expected version starting with ${TENSORFLOW_VERSION}, got {tf.__version__}'" && \
   # Test 2: Check GPU support based on base image
   if [[ "${VAST_BASE}" == *"cuda"* ]]; then \
       python3 -c "import tensorflow as tf; print('TF Build:', tf.version.GIT_VERSION, tf.version.VERSION); print('Compiler:', tf.version.COMPILER_VERSION); assert hasattr(tf, 'config') and hasattr(tf.config, 'list_physical_devices'), 'GPU support not properly built'"; \
   elif [[ "${VAST_BASE}" == *"rocm"* ]]; then \
       python3 -c "import tensorflow as tf; build_info = tf.sysconfig.get_build_info(); assert any('rocm' in str(v).lower() for v in build_info.values()), 'ROCm support not found in TensorFlow build'; print('Build info:', build_info)"; \
   fi && \
   # Test 3: Basic tensor operations
   python3 -c "import tensorflow as tf; x = tf.random.normal((2,3)); y = tf.random.normal((3,2)); z = tf.matmul(x, y); assert z.shape == (2,2), 'Basic tensor operations failed'" && \
   # Test 4: Simple model creation and compilation
   python3 -c "import tensorflow as tf; model = tf.keras.Sequential([tf.keras.layers.Dense(2, input_shape=(10,))]); model.compile(optimizer='adam', loss='mse'); print('✓ Model creation successful')"

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash