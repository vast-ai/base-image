#!/bin/bash

# Function to log errors
log_error() {
   echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" >> log.txt
}

# Function to build docker image
build_image() {
    local dockerfile="$1"
    local tag_repo="$2" 
    local base_image="$3"
    local arch="$4"
    local python_version="$5"
    local tag_pattern="$6"
    local additional_tags="$7"
    local from_repo="$8"
    
    # Build the primary tag using the tag pattern
    local py_version_tag="${python_version//./}"
    local primary_tag=$(eval echo "$tag_pattern")
    
    # Determine the actual base image to use
    local actual_base_image="$base_image"
    if [[ "$from_repo" == "true" ]]; then
        actual_base_image="$primary_tag"
        echo "Building from existing repo image: $actual_base_image"
    else
        echo "Building from base image: $actual_base_image"
    fi
    
    # Start building the docker command
    local docker_cmd="docker buildx build --progress=plain"
    docker_cmd+=" --build-arg BASE_IMAGE=${actual_base_image}"
    docker_cmd+=" --build-arg PYTHON_VERSION=${python_version}"
    docker_cmd+=" --platform ${arch}"
    docker_cmd+=" -f ${dockerfile}"
    docker_cmd+=" --tag ${primary_tag}"
    
    # Add additional tags if provided
    if [[ -n "$additional_tags" ]]; then
        IFS=',' read -ra TAGS <<< "$additional_tags"
        for tag in "${TAGS[@]}"; do
            local additional_tag=$(eval echo "$tag")
            docker_cmd+=" --tag ${additional_tag}"
        done
    fi
    
    docker_cmd+=" --push ."
    
    echo "Building: $primary_tag"
    if [[ -n "$additional_tags" ]]; then
        echo "Additional tags: $additional_tags"
    fi
    
    # Check if this is a dry run
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "DRY RUN - Would execute:"
        echo "$docker_cmd"
        echo ""
    else
        eval "$docker_cmd" || {
            log_error "Failed to build ${primary_tag}"
            exit 1
        }
    fi
}

# Function to version_in_range - check if version is in specified range
version_in_range() {
    local version="$1"
    local min_version="$2"
    local max_version="$3"
    
    # Convert versions to comparable format (e.g., 3.10 -> 310)
    local version_num=$(echo "$version" | tr -d '.')
    local min_num=$(echo "$min_version" | tr -d '.')
    local max_num=$(echo "$max_version" | tr -d '.')
    
    [[ $version_num -ge $min_num && $version_num -le $max_num ]]
}

# Function to check if ubuntu version is supported for a given cuda version
ubuntu_supported() {
    local ubuntu_ver="$1"
    local cuda_ver="$2"
    local image_type="$3"
    
    case "$image_type" in
        "stock"|"rocm")
            # Stock and ROCm support both ubuntu 22 and 24
            [[ "$ubuntu_ver" == "22" || "$ubuntu_ver" == "24" ]]
            ;;
        "cuda")
            if [[ "$ubuntu_ver" == "22" ]]; then
                # Ubuntu 22 supported for all CUDA versions
                return 0
            elif [[ "$ubuntu_ver" == "24" ]]; then
                # Ubuntu 24 only for CUDA 12.6+
                local cuda_major=$(echo "$cuda_ver" | cut -d'.' -f1)
                local cuda_minor=$(echo "$cuda_ver" | cut -d'.' -f2)
                [[ $cuda_major -gt 12 || ($cuda_major -eq 12 && $cuda_minor -ge 6) ]]
            else
                return 1
            fi
            ;;
        *)
            return 1
            ;;
    esac
}

# Build Configuration - format: key|base_image|tag_template|arch|min_py|max_py
BUILD_CONFIGS=(
    "stock-22|ubuntu:22.04|stock-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "stock-24|ubuntu:24.04|stock-ubuntu24.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "rocm-22|rocm/dev-ubuntu-22.04:6.2.4-complete|rocm-dev-ubuntu-22.04-6.2.4-complete-py\${py_version_tag}|linux/amd64|3.7|3.14"
    "rocm-24|rocm/dev-ubuntu-24.04:6.2.4-complete|rocm-dev-ubuntu-24.04-6.2.4-complete-py\${py_version_tag}|linux/amd64|3.7|3.14"
    "cuda-11.8-22|nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04|cuda-11.8.0-cudnn8-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.1-22|nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04|cuda-12.1.1-cudnn8-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.4-22|nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04|cuda-12.4.1-cudnn-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.6-22|nvidia/cuda:12.6.3-cudnn-devel-ubuntu22.04|cuda-12.6.3-cudnn-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.6-24|nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04|cuda-12.6.3-cudnn-devel-ubuntu24.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.8-22|nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04|cuda-12.8.1-cudnn-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.8-24|nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04|cuda-12.8.1-cudnn-devel-ubuntu24.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.9-22|nvidia/cuda:12.9.1-cudnn-devel-ubuntu22.04|cuda-12.9.1-cudnn-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-12.9-24|nvidia/cuda:12.9.1-cudnn-devel-ubuntu24.04|cuda-12.9.1-cudnn-devel-ubuntu24.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-13.0.1-22|nvidia/cuda:13.0.1-cudnn-devel-ubuntu22.04|cuda-13.0.1-cudnn-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-13.0.1-24|nvidia/cuda:13.0.1-cudnn-devel-ubuntu24.04|cuda-13.0.1-cudnn-devel-ubuntu24.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-13.0.2-22|nvidia/cuda:13.0.2-cudnn-devel-ubuntu22.04|cuda-13.0.2-cudnn-devel-ubuntu22.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
    "cuda-13.0.2-24|nvidia/cuda:13.0.2-cudnn-devel-ubuntu24.04|cuda-13.0.2-cudnn-devel-ubuntu24.04-py\${py_version_tag}|linux/amd64,linux/arm64|3.7|3.14"
)

# Parse command line arguments
DOCKERFILE="Dockerfile"
TAG_REPO="robatvastai/base-image"
FROM_REPO="false"
BUILD_FILTER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --dockerfile)
            DOCKERFILE="$2"
            shift 2
            ;;
        --tagrepo)
            TAG_REPO="$2"
            shift 2
            ;;
        --from-repo)
            FROM_REPO="true"
            shift
            ;;
        --filter)
            BUILD_FILTER="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        --list)
            echo "Available build configurations:"
            for config_line in "${BUILD_CONFIGS[@]}"; do
                IFS='|' read -ra CONFIG <<< "$config_line"
                config_key="${CONFIG[0]}"
                base_image="${CONFIG[1]}"
                tag_template="${CONFIG[2]}"
                min_python="${CONFIG[4]}"
                max_python="${CONFIG[5]}"
                echo "  $config_key: $base_image -> $tag_template (Python $min_python-$max_python)"
            done
            exit 0
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dockerfile <file>     Dockerfile to use (default: Dockerfile)"
            echo "  --tagrepo <repo/image>  Repository/image name for tags (default:  robatvastai/base-image)"
            echo "  --from-repo             Build from existing repo images instead of base images"
            echo "  --filter <pattern>      Only build configurations matching pattern (e.g., 'cuda-12', 'stock', 'ubuntu24')"
            echo "  --list                  List all available build configurations"
            echo "  -h, --help              Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                                    # Build all configurations"
            echo "  $0 --filter cuda-12                  # Build only CUDA 12.x versions"
            echo "  $0 --filter 24                       # Build only Ubuntu 24 versions"
            echo "  $0 --filter stock                    # Build only stock Ubuntu versions"
            echo "  $0 --from-repo --filter cuda-11.8    # Add layers to existing CUDA 11.8 images"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "Using dockerfile: $DOCKERFILE"
echo "Using tag repository: $TAG_REPO"
echo "Build from repo: $FROM_REPO"
if [[ -n "$BUILD_FILTER" ]]; then
    echo "Build filter: $BUILD_FILTER"
fi
echo ""

echo "Starting builds..."
echo ""

# Iterate through build configurations in order
for config_line in "${BUILD_CONFIGS[@]}"; do
    # Parse configuration
    IFS='|' read -ra CONFIG <<< "$config_line"
    config_key="${CONFIG[0]}"
    BASE_IMAGE="${CONFIG[1]}"
    TAG_PATTERN="\${TAG_REPO}:${CONFIG[2]}"
    ARCH="${CONFIG[3]}"
    MIN_PYTHON="${CONFIG[4]}"
    MAX_PYTHON="${CONFIG[5]}"
    
    # Apply filter if specified
    if [[ -n "$BUILD_FILTER" && "$config_key" != *"$BUILD_FILTER"* ]]; then
        continue
    fi
    
    echo "=== Building configuration: $config_key ==="
    
    echo "Base image: $BASE_IMAGE"
    echo "Tag pattern: $TAG_PATTERN"
    echo "Python versions: $MIN_PYTHON - $MAX_PYTHON"
    echo ""
    
    # Determine Ubuntu version and default Python version
    ubuntu_version=""
    default_python=""
    
    if [[ "$config_key" == *"-22" ]]; then
        ubuntu_version="22"
        default_python="3.10"  # Ubuntu 22.04 ships with Python 3.10
    elif [[ "$config_key" == *"-24" ]]; then
        ubuntu_version="24"
        default_python="3.12"  # Ubuntu 24.04 ships with Python 3.12
    fi
    
    # Build for each Python version in range
    ALL_PYTHON_VERSIONS=("3.7" "3.8" "3.9" "3.10" "3.11" "3.12" "3.13" "3.14")
    
    for py_version in "${ALL_PYTHON_VERSIONS[@]}"; do
        if version_in_range "$py_version" "$MIN_PYTHON" "$MAX_PYTHON"; then
            py_version_tag="${py_version//./}"
            
            # Add additional tag for default Python version of the Ubuntu distro
            additional_tags=""
            if [[ "$py_version" == "$default_python" && -n "$ubuntu_version" ]]; then
                # Create additional tag by removing the Python version suffix from the tag pattern
                base_tag_pattern="${CONFIG[2]}"
                # Remove the -py* suffix using sed for reliable pattern replacement
                additional_tag_pattern=$(echo "$base_tag_pattern" | sed 's/-py.*$//')
                additional_tags="\${TAG_REPO}:${additional_tag_pattern}"
                echo "Adding default Python tag for Ubuntu $ubuntu_version: $(eval echo "$additional_tags")"
            fi
            
            build_image "$DOCKERFILE" "$TAG_REPO" "$BASE_IMAGE" "$ARCH" "$py_version" "$TAG_PATTERN" "$additional_tags" "$FROM_REPO"
            echo ""
        fi
    done
    
    echo "Completed configuration: $config_key"
    echo ""
done

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run completed! All build commands have been printed."
else
    echo "All builds completed successfully!"
fi