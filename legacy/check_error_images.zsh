#!/usr/bin/env zsh

# Define colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to wrap text in blue
info() {
    echo -e "${BLUE}$1${NC}"
}

# Function to wrap text in red
error() {
    echo -e "${RED}$1${NC}"
}

# Function to wrap text in green
success() {
    echo -e "${GREEN}$1${NC}"
}

PROCESSING_DIR="$HOME/Pictures/wallpaper/processing"
ORIG_STORE_DIR="$PROCESSING_DIR/originals"
ORIG_ERROR_DIR="$PROCESSING_DIR/error"
DESKTOP_OUTPUT_DIR="$PROCESSING_DIR/to_sort_desktop/4x_upscaled"
PHONE_OUTPUT_DIR="$PROCESSING_DIR/to_sort_phone/4x_upscaled"

# Ensure output directory exists
create_output_directory() {
    local output_dir="$1"
    mkdir -p "$output_dir"
}

# Get image dimensions using ImageMagick
get_image_dimensions() {
    local image="$1"
    magick identify -format "%wx%h" "$image"
}

# Calculate the aspect ratio
calculate_aspect_ratio() {
    local width="$1"
    local height="$2"
    echo "scale=2; $width / $height" | bc
}

run_resize_apple_script() {
    local input_image="$1"
    local new_size="$2"
    local resize_dimension="$3"
    local output_dir="$4"
    local device="$5"

    local basename=$(basename "$input_image" | sed 's/\.[^.]*$//')
    local output_path="${output_dir}/${basename}_${device}.heic"

    echo "Output path: $output_path"

    # Ensure the output directory is writable
    if [ ! -w "$output_dir" ]; then
        error "Output directory '$output_dir' is not writable."
        return 1
    fi

    # Convert input_image to an absolute path
    local abs_input_image=$(cd "$(dirname "$input_image")"; pwd)/$(basename "$input_image")

    local app="Pixelmator Pro"
    osascript -e "tell application \"$app\" to open \"$abs_input_image\""
    osascript -e "tell application \"$app\"
    tell the front document to resize image $resize_dimension $new_size resolution 72 algorithm ml super resolution
    set filePath to POSIX file \"$output_path\" as text
    export the front document to file filePath as HEIC with properties {compression factor:85}
    close the front document saving no
end tell"
}

# Helper function to handle the resizing logic
check_target_image() {
    local input_image="$1"
    local target_size="$2"
    local dimension="$3"
    local output_dir="$4"
    local ratio="$5"
    local device="$6"

    if (( $(echo "$ratio <= 4.25" | bc -l) )); then
        run_resize_apple_script "$input_image" "$target_size" "$dimension" "$output_dir" "$device"
    else
        error "$(basename "$input_image") is too small to upscale nicely..."
        error "Skipping..."
        # mv "$input_image" "$ORIG_ERROR_DIR"
    fi
}

# Main function to process the image
process_image() {
    local input_image="$1"

    create_output_directory "$DESKTOP_OUTPUT_DIR"
    create_output_directory "$PHONE_OUTPUT_DIR"

    # Get image dimensions
    local dimensions=$(get_image_dimensions "$input_image")

    # Extract width and height
    local width=${dimensions%x*}
    local height=${dimensions#*x}

    # Calculate aspect ratio
    local aspect_ratio=$(calculate_aspect_ratio "$width" "$height")

    # Set the resize dimensions based on aspect ratio
    local desktop_width=5120
    local desktop_height=3200
    local phone_width=2560
    local phone_height=3840

    if (( $(echo "$aspect_ratio >= 1" | bc -l) )); then
        info "$(basename "$input_image") aspect ratio is greater than or equal to 1. Resizing for desktop..."
        if (( $(echo "$aspect_ratio < 16 / 10" | bc -l) )); then
            local desktop_ratio=$(echo "$desktop_width / $width" | bc -l)
            check_target_image "$input_image" "$desktop_width" "width" "$DESKTOP_OUTPUT_DIR" "$desktop_ratio" "desktop"
        else
            local desktop_ratio=$(echo "$desktop_height / $height" | bc -l)
            check_target_image "$input_image" "$desktop_height" "height" "$DESKTOP_OUTPUT_DIR" "$desktop_ratio" "desktop"
        fi
    else
        info "$(basename "$input_image") aspect ratio is less than 1. Resizing for phone..."
        if (( $(echo "$aspect_ratio <= 2 / 3" | bc -l) )); then
            local phone_ratio=$(echo "$phone_width / $width" | bc -l)
            check_target_image "$input_image" "$phone_width" "width" "$PHONE_OUTPUT_DIR" "$phone_ratio" "phone"
        else
            local phone_ratio=$(echo "$phone_height / $height" | bc -l)
            check_target_image "$input_image" "$phone_height" "height" "$PHONE_OUTPUT_DIR" "$phone_ratio" "phone"
        fi
    fi
}

# Check if the script received an input image
if [ $# -eq 0 ]; then
    echo "Usage: $0 <image_file>"
    exit 1
fi

input_path="$1"

# Check if the input is a directory
if [ -d "$input_path" ]; then
    for image in "$input_path"/*; do
        if [ -f "$image" ]; then
            process_image "$image"
        fi
    done
elif [ -f "$input_path" ]; then
    process_image "$input_path"
else
    echo "Error: $input_path is neither a file nor a directory."
    exit 1
fi
