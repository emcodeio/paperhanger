#!/usr/bin/env zsh

# Define colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to wrap text in blue
function info() {
    echo -e "${BLUE}$1${NC}"
}

# Function to wrap text in red
function error() {
    echo -e "${RED}$1${NC}"
}

# Function to wrap text in green
function success() {
    echo -e "${GREEN}$1${NC}"
}

# Define the paths and constans
PROCESSING_DIR="$HOME/Pictures/wallpaper/processing"
ORIG_STORE_DIR="$PROCESSING_DIR/originals"
ORIG_ERROR_DIR="$PROCESSING_DIR/error"
DESKTOP_OUTPUT_DIR="$PROCESSING_DIR/to_sort_desktop"
PHONE_OUTPUT_DIR="$PROCESSING_DIR/to_sort_phone"
DESKTOP_4X_DIR="$DESKTOP_OUTPUT_DIR/4x_upscaled"
PHONE_4X_DIR="$PHONE_OUTPUT_DIR/4x_upscaled"
MAX_DESKTOP_WIDTH=7680
MAX_DESKTOP_HEIGHT=4800
MIN_DESKTOP_WIDTH=5120
MIN_DESKTOP_HEIGHT=3200
MAX_PHONE_WIDTH=5120
MAX_PHONE_HEIGHT=7680
MIN_PHONE_WIDTH=2560
MIN_PHONE_HEIGHT=3840

# Ensure output directory exists
function create_output_directory() {
    local output_dir="$1"
    mkdir -p "$output_dir"
}

# Clean up temporary resized images
function cleanup_temp_files() {
    local temp_files=("$@")
    for file in "${temp_files[@]}"; do
        rm "$file"
    done
}

function get_image_width() {
    local image="$1"
    magick identify -format "%w" "$image"
}

function get_image_height() {
    local image="$1"
    magick identify -format "%h" "$image"
}

function get_aspect_ratio() {
    local image="$1"
    local width=$(get_image_width $image)
    local height=$(get_image_height $image)
    echo "scale=2; $width / $height" | bc
}

# TODO: fix downscaling_image. Add device to name and move downscaled image to correct directory

function downscale_image() {
    local image="$1"
    local resized_res="$2"
    local dimension="$3"
    local device="$4"
    magick "$image" -filter Lanczos -resize "$dimension" "$resized_res"
}

function upscale_image() {
    local image="$1"
    local new_size="$2"
    local resize_dimension="$3"
    local output_dir="$4"
    local device="$5"

    local basename=$(basename "$image" | sed 's/\.[^.]*$//')
    local output_path="${output_dir}/${basename}_ml_res_${device}.heic"

    local abs_image_path=$(cd "$(dirname "$image")"; pwd)/$(basename "$image")

    local app="Pixelmator Pro"
    osascript -e "tell application \"$app\" to open \"$abs_image_path\""
    osascript -e "tell application \"$app\"
    tell the front document to resize image $resize_dimension $new_size resolution 72 algorithm ml super resolution
    set filePath to POSIX file \"$output_path\" as text
    export the front document to file filePath as HEIC with properties {compression factor:85}
    close the front document saving no
end tell"
}

function crop_image_into_thirds() {
    local image="$1"
    local dimension="$2"
    local aspect_numerator="$3"
    local aspect_denominator="$4"
    local input_dir="${image:h}/"
    local input_basename=$(basename "$image" | sed 's/\.[^.]*$//')
    # local input_png="${input_dir}${input_basename}.png"

    local width=$(get_image_width $image)
    local height=$(get_image_height $image)

    # magick "$image" "$input_png"

    local output_image_1
    local output_image_2
    local output_image_3
    local crop_dimension
    local start_1
    local start_2
    local start_3
    local crop_size
    local start_position
    local process_type

    if [[ "$dimension" == "horizontal" ]]; then
        output_image_1="${input_dir}${input_basename}_top.png"
        output_image_2="${input_dir}${input_basename}_middle.png"
        output_image_3="${input_dir}${input_basename}_bottom.png"

        crop_dimension=$((width * aspect_denominator / aspect_numerator))
        start_1=0
        start_2=$(((height / 2) - (crop_dimension / 2)))
        start_3=$((height - crop_dimension))

        crop_size="${width}x${crop_dimension}"
        start_position=("0+${start_1}" "0+${start_2}" "0+${start_3}")
        process_type="d"
    else
        output_image_1="${input_dir}${input_basename}_left.png"
        output_image_2="${input_dir}${input_basename}_center.png"
        output_image_3="${input_dir}${input_basename}_right.png"

        crop_dimension=$((height * aspect_numerator / aspect_denominator))
        start_1=0
        start_2=$(((width / 2) - (crop_dimension / 2)))
        start_3=$((width - crop_dimension))

        crop_size="${crop_dimension}x${height}"
        start_position=("${start_1}+0" "${start_2}+0" "${start_3}+0")
        process_type="p"
    fi

    local output_images=("$output_image_1" "$output_image_2" "$output_image_3")

    for i in {1..3}; do
        magick "$image" -crop "${crop_size}+${start_position[i]}" "${output_images[i]}"
    done

    info "Crops completed. Created the following images:"
    for output_image in "${output_images[@]}"; do
        echo "$output_image"
    done

    info "Upscaling cropped images..."
    for output_image in "${output_images[@]}"; do
        process_image "$process_type" "$output_image"
    done

    info "Removing temporary cropped images..."
    # rm "$input_png"
    for output_image in "${output_images[@]}"; do
        output_file="$ORIG_STORE_DIR/$(basename "$output_image")"
        if [ -e "$output_file" ]; then
            rm "$output_file"
        fi
    done

    info "Continuing..."
}

function crop_image_into_horizontal_thirds() {
    crop_image_into_thirds "$1" "horizontal" 16 10
}

function crop_image_into_vertical_thirds() {
    crop_image_into_thirds "$1" "vertical" 2 3
}

function resize_desktop_based_on_width() {
    local image=$1
    local width=$(get_image_width $image)

    if [[ $(echo "$(get_aspect_ratio $image) > 1" | bc -l) -eq 1 ]]; then
        echo "$(basename "$image") is too short to horizontally crop three times. Continuing..."
        # if (( width >= 7680 )); then
        #     info "$(basename "$image") is already large enough. Skipping Super Resolution..."
        #     info "Downscaling $(basename "$image") to target size..."
        #     downscale_image $image 7680 "width" $DESKTOP_OUTPUT_DIR
        if (( (width * 3) >= 7680 )); then
            info "Applying Super Resolution to $(basename "$image")..."
            upscale_image $image 7680 "width" $DESKTOP_OUTPUT_DIR "lt_3x_desktop"
        elif (( (width * 3) < 7680 && (width * 3) >= 5120 )); then
            info "Applying Super Resolution to $(basename "$image")..."
            upscale_image $image $((width * 3)) "width" $DESKTOP_OUTPUT_DIR "3x_desktop"
        elif (( (width * 4.25) >= 5120 )); then
            error "$(basename "$image") will not be large enough even after applying 3x Super Resolution."
            info "Trying 4x Super Resolution..."
            upscale_image $image 5120 "width" $DESKTOP_4X_DIR "4x_desktop"
        else
            error "$(basename "$image") is too small to upscale nicely..."
            error "Skipping..."
            mv "$image" "$ORIG_ERROR_DIR"
            echo
            return 1
        fi
    else
        echo "$(basename "$image") is tall enough to horizontally crop three times. Cropping..."
        crop_image_into_horizontal_thirds $image
    fi
}

function resize_desktop_based_on_height() {
    local image=$1
    local height=$(get_image_height $image)

    # if (( height >= 4800 )); then
    #     info "$(basename "$image") is already large enough. Skipping Super Resolution..."
    #     info "Downscaling $(basename "$super_res_image") to target size..."
    #     downscale_image_height $image 4800 "height" $DESKTOP_OUTPUT_DIR
    if (( (height * 3) >= 4800 )); then
        info "Applying Super Resolution to $(basename "$image")..."
        upscale_image $image 4800 "height" $DESKTOP_OUTPUT_DIR "lt_3x_desktop"
    elif (( (height * 3) < 4800 && (height * 3) >= 3200 )); then
        info "Applying Super Resolution to $(basename "$image")..."
        upscale_image $image $((height * 3)) "height" $DESKTOP_OUTPUT_DIR "3x_desktop"
    elif (( (height * 4.25) >= 3200 )); then
        error "$(basename "$image") will not be large enough even after applying 3x Super Resolution."
        info "Trying 4x Super Resolution..."
        upscale_image $image 3200 "height" $DESKTOP_4X_DIR "4x_desktop"
    else
        error "$(basename "$image") is too small to upscale nicely..."
        error "Skipping..."
        mv "$image" "$ORIG_ERROR_DIR"
        echo
        return 1
    fi
}

function resize_phone_based_on_width() {
    local image=$1
    local width=$(get_image_width $image)

    # if (( width >= 2560 )); then
    #     info "$(basename "$image") is already large enough. Skipping Super Resolution..."
    #     info "Downscaling $(basename "$image") to target size..."
    #     downscale_image $image 2560 "width" $PHONE_OUTPUT_DIR
    if (( (width * 3) >= 2560 )); then
        info "Applying Super Resolution to $(basename "$image")..."
        upscale_image $image 2560 "width" $PHONE_OUTPUT_DIR "lt_3x_phone"
    elif (( (width * 4.25) >= 2560 )); then
        error "$(basename "$image") will not be large enough even after applying 3x Super Resolution."
        info "Trying 4x Super Resolution..."
        upscale_image $image 2560 "width" $PHONE_4X_DIR "4x_phone"
    else
        error "$(basename "$image") is too small to upscale nicely..."
        error "Skipping..."
        mv "$image" "$ORIG_ERROR_DIR"
        echo
        return 1
    fi
}

function resize_phone_based_on_height() {
    local image=$1
    local height=$(get_image_height $image)

    if [[ $(echo "$(get_aspect_ratio $image) < 1" | bc -l) -eq 1 ]]; then
        echo "$(basename "$image") is too thin to vertically crop three times. Continuing..."
        # if (( height >= 3840 )); then
        #     info "$(basename "$image") is already large enough. Skipping Super Resolution..."
        #     info "Downscaling $(basename "$image") to target size..."
        #     downscale_image $image 3840 "height" $PHONE_OUTPUT_DIR
        if (( (height * 3) >= 3840 )); then
            info "Applying Super Resolution to $(basename "$image")..."
            upscale_image $image 3840 "height" $PHONE_OUTPUT_DIR "lt_3x_phone"
        elif (( (height * 4.25) >= 3840 )); then
            error "$(basename "$image") will not be large enough even after applying 3x Super Resolution."
            info "Trying 4x Super Resolution..."
            upscale_image $image 3840 "height" $PHONE_4X_DIR "4x_phone"
        else
            error "$(basename "$image") is too small to upscale nicely..."
            error "Skipping..."
            mv "$image" "$ORIG_ERROR_DIR"
            echo
            return 1
        fi
    else
        echo "$(basename "$image") is wide enough to vertically crop three times. Cropping..."
        crop_image_into_vertical_thirds $image
    fi
}

# Desktop Resizing Logic
function process_desktop_image() {
    local image=$1

    if [[ $(echo "$(get_aspect_ratio $image) <= (16/10)" | bc -l) -eq 1 ]]; then
        info "$(basename "$image") aspect ratio is less than or equal to 16:10. Resizing based on width..."
        resize_desktop_based_on_width $image
        if [ $? -ne 0 ]; then return 1; fi
    else
        info "$(basename "$image") aspect ratio is greater than 16:10. Resizing based on height..."
        resize_desktop_based_on_height $image
        if [ $? -ne 0 ]; then return 1; fi
    fi
}

# Phone Resizing Logic
function process_phone_image() {
    local image=$1

    if [[ $(echo "$(get_aspect_ratio $image) >= (2/3)" | bc -l) -eq 1 ]]; then
        info "$(basename "$image") aspect ratio is greater than or equal to 2:3. Resizing based on height..."
        resize_phone_based_on_height $image
        if [ $? -ne 0 ]; then return 1; fi
    else
        info "$(basename "$image") aspect ratio is less than 2:3. Resizing based on width..."
        resize_phone_based_on_width $image
        if [ $? -ne 0 ]; then return 1; fi
    fi
}

function create_output_directories() {
    create_output_directory "$PROCESSING_DIR"
    create_output_directory "$ORIG_STORE_DIR"
    create_output_directory "$ORIG_ERROR_DIR"
    create_output_directory "$DESKTOP_OUTPUT_DIR"
    create_output_directory "$PHONE_OUTPUT_DIR"
    create_output_directory "$DESKTOP_4X_DIR"
    create_output_directory "$PHONE_4X_DIR"
}

# Process a single image
function process_image() {
    local process_type="${1:-both}"  # Default to "both" if no parameter is provided
    local image="$2"

    create_output_directories

    if [[ "$process_type" == "d" || "$process_type" == "both" ]]; then
        process_desktop_image "$image"
        if [ $? -ne 0 ]; then return 1; fi
    fi

    if [[ "$process_type" == "p" || "$process_type" == "both" ]]; then
        process_phone_image "$image"
        if [ $? -ne 0 ]; then return 1; fi
    fi

    success "Processing complete for $image."
    if [ -e "$image" ]; then mv "$image" "$ORIG_STORE_DIR"; fi
    echo
}

# Function to display usage
function usage() {
    echo "Usage: $0 [-d | -p | -b] <input-image-or-directory>"
    echo "  -d    Process only desktop images"
    echo "  -p    Process only phone images"
    echo "  -b    Process both desktop and phone images (default)"
    exit 1
}

# Default process type
process_type="both"

# Parse flags
while getopts ":dpb" opt; do
  case $opt in
    d) process_type="d"
       ;;
    p) process_type="p"
       ;;
    b) process_type="both"
       ;;
    *) usage
       ;;
  esac
done
shift $((OPTIND -1))

# Check if the input image or directory is provided
if [ -z "$1" ]; then
    usage
fi

input_path="$1"

# Check if the input is a directory
if [ -d "$input_path" ]; then
    for image in "$input_path"/*; do
        if [ -f "$image" ]; then
            process_image "$process_type" "$image"
        fi
    done
elif [ -f "$input_path" ]; then
    process_image "$process_type" "$input_path"
else
    echo "Error: $input_path is neither a file nor a directory."
    exit 1
fi
