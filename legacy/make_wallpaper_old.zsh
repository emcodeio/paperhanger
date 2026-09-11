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

# Define the paths and constans
SUPER_RES_WORKFLOW="$HOME/.dotfiles/bin/shell_scripts/make_wallpaper/apply_super_res_wallpaper_safe.workflow"
CONVERT_HEIC_WORKFLOW="$HOME/.dotfiles/bin/shell_scripts/make_wallpaper/convert_to_heic.workflow"
PROCESSING_DIR="$HOME/Pictures/wallpaper/processing"
ORIG_STORE_DIR="$PROCESSING_DIR/originals"
ORIG_ERROR_DIR="$PROCESSING_DIR/error"
DESKTOP_OUTPUT_DIR="$PROCESSING_DIR/to_sort_desktop"
PHONE_OUTPUT_DIR="$PROCESSING_DIR/to_sort_phone"
DESKTOP_FOUR_X_DIR="$DESKTOP_OUTPUT_DIR/4x_upscaled"
PHONE_FOUR_X_DIR="$PHONE_OUTPUT_DIR/4x_upscaled"

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

# Apply Super Resolution workflow
apply_super_resolution() {
    local input_image="$1"
    automator -i "$input_image" "$SUPER_RES_WORKFLOW"
}

# Resize the image while maintaining the aspect ratio
resize_image() {
    local input_image="$1"
    local resized_image="$2"
    local dimension="$3"
    magick "$input_image" -filter Lanczos -resize "$dimension" "$resized_image"
}

# Verify if the super resolution workflow generated the expected output
verify_super_resolution_output() {
    local super_res_image="$1"
    if [ ! -f "$super_res_image" ]; then
        error "Super resolution workflow did not generate the expected output for $super_res_image."
        return 1
    fi
}

# Apply Convert to HEIC workflow
apply_convert_heic() {
    local resized_image="$1"
    automator -i "$resized_image" "$CONVERT_HEIC_WORKFLOW"
}

# Clean up temporary resized images
cleanup_temp_files() {
    local temp_files=("$@")
    for file in "${temp_files[@]}"; do
        rm "$file"
    done
}

crop_image_into_thirds() {
    local input_image="$1"
    local width="$2"
    local height="$3"
    local direction="$4"
    local aspect_numerator="$5"
    local aspect_denominator="$6"
    local input_dir="${input_image:h}/"
    local input_basename=$(basename "$input_image" | sed 's/\.[^.]*$//')
    local input_png="${input_dir}${input_basename}.png"

    magick "$input_image" "$input_png"

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

    if [[ "$direction" == "horizontal" ]]; then
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
        magick "$input_png" -crop "${crop_size}+${start_position[i]}" "${output_images[i]}"
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
    rm "$input_png"
    for output_image in "${output_images[@]}"; do
        output_file="$ORIG_STORE_DIR/$(basename "$output_image")"
        if [ -e "$output_file" ]; then
            rm "$output_file"
        fi
    done

    info "Continuing..."
}

# Wrapper functions
crop_image_into_horizontal_thirds() {
    crop_image_into_thirds "$1" "$2" "$3" "horizontal" 16 10
}

crop_image_into_vertical_thirds() {
    crop_image_into_thirds "$1" "$2" "$3" "vertical" 2 3
}

# Handle Super Resolution process
handle_super_resolution() {
    local input_image="$1"
    local resized_image="$2"
    local super_res_image="$3"
    local target_min_dim="$4"
    local target_max_dim="$5"
    local resize_dim="$6"
    local dim_index="$7"

    info "Applying Super Resolution to $(basename "$input_image")..."
    apply_super_resolution "$input_image"

    if ! verify_super_resolution_output "$super_res_image"; then
        return 1
    fi

    local super_res_dim
    super_res_dim=$(get_image_dimensions "$super_res_image" | cut -d'x' -f"$dim_index")

    if [ "$super_res_dim" -lt "$target_min_dim" ]; then
        error "$(basename "$super_res_image") is still not large enough after applying Super Resolution."
        mv "$input_image" "$ORIG_ERROR_DIR"
        cleanup_temp_files "$super_res_image"
        return 1
    fi

    info "$(basename "$super_res_image") is large enough after applying Super Resolution. Continuing..."

    if [ "$super_res_dim" -ge "$target_max_dim" ]; then
        info "Downscaling $(basename "$super_res_image") to target size..."
        resize_image "$super_res_image" "$resized_image" "$resize_dim"
    else
        info "Renaming $(basename "$super_res_image") to $(basename "$resized_image")"
        cp "$super_res_image" "$resized_image"
    fi

    cleanup_temp_files "$super_res_image"
}

resize_4x_apple_script() {
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
        error "Output directory '$output_path' is not writable."
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
check_4x_image() {
    local input_image="$1"
    local target_size="$2"
    local dimension="$3"
    local output_dir="$4"
    local ratio="$5"
    local device="$6"

    if (( $(echo "$ratio <= 4.25" | bc -l) )); then
        resize_4x_apple_script "$input_image" "$target_size" "$dimension" "$output_dir" "$device"
    else
        error "$(basename "$input_image") is too small to upscale nicely..."
        error "Skipping..."
        mv "$input_image" "$ORIG_ERROR_DIR"
        return 1
    fi
}

# Main function to process the image
create_4x_image() {
    local input_image="$1"

    create_output_directory "$DESKTOP_FOUR_X_DIR"
    create_output_directory "$PHONE_FOUR_X_DIR"

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
            check_4x_image "$input_image" "$desktop_width" "width" "$DESKTOP_FOUR_X_DIR" "$desktop_ratio" "desktop"
            if [ $? -ne 0 ]; then return 1; fi
        else
            local desktop_ratio=$(echo "$desktop_height / $height" | bc -l)
            check_4x_image "$input_image" "$desktop_height" "height" "$DESKTOP_FOUR_X_DIR" "$desktop_ratio" "desktop"
            if [ $? -ne 0 ]; then return 1; fi
        fi
    else
        info "$(basename "$input_image") aspect ratio is less than 1. Resizing for phone..."
        if (( $(echo "$aspect_ratio <= 2 / 3" | bc -l) )); then
            local phone_ratio=$(echo "$phone_width / $width" | bc -l)
            check_4x_image "$input_image" "$phone_width" "width" "$PHONE_FOUR_X_DIR" "$phone_ratio" "phone"
            if [ $? -ne 0 ]; then return 1; fi
        else
            local phone_ratio=$(echo "$phone_height / $height" | bc -l)
            check_4x_image "$input_image" "$phone_height" "height" "$PHONE_FOUR_X_DIR" "$phone_ratio" "phone"
            if [ $? -ne 0 ]; then return 1; fi
        fi
    fi
}

# Generic function to process image
create_image() {
    local input_image="$1"
    local resized_image="$2"
    local super_res_image="$3"
    local image_dim="$4"
    local target_min_dim="$5"
    local target_max_dim="$6"
    local resize_dim="$7"
    local dim_index="$8"

    local target_output_dim=$((image_dim * 3))

    if (( target_output_dim < target_min_dim )); then
        error "$(basename "$input_image") will not be large enough even after applying 3x Super Resolution."
        info "Trying 4x Super Resolution..."
        create_4x_image "$input_image"
        if [ $? -ne 0 ]; then return 1; fi
        return 0
    fi

    if [ "$image_dim" -ge "$target_max_dim" ]; then
        info "$(basename "$input_image") is already large enough. Skipping Super Resolution..."
        info "Downscaling $(basename "$super_res_image") to target size..."
        resize_image "$input_image" "$resized_image" "$resize_dim"
    else
        handle_super_resolution "$input_image" "$resized_image" "$super_res_image" "$target_min_dim" "$target_max_dim" "$resize_dim" "$dim_index"
        if [ $? -ne 0 ]; then return 1; fi
    fi
}

# Function to process the desktop image based on aspect ratio
process_desktop_image() {
    local input_image="$1"
    local super_res_image="$2"
    local resized_desktop_image="$3"
    local image_width="$4"
    local image_height="$5"
    local aspect_ratio="$6"

    if (( $(echo "$aspect_ratio <= 16 / 10" | bc -l) )); then
        info "$(basename "$input_image") aspect ratio is less than or equal to 16:10. Resizing based on width..."
        # if (( $(echo "$aspect_ratio > 4 / 5" | bc -l) )); then
        if (( $(echo "$aspect_ratio > 1" | bc -l) )); then
            echo "$(basename "$input_image") is too short to horizontally crop three times. Continuing..."
            create_image "$input_image" "$resized_desktop_image" "$super_res_image" "$image_width" 5120 7680 "7680x" 1
            if [ $? -ne 0 ]; then return 1; fi
            apply_convert_heic "$resized_desktop_image"
        else
            echo "$(basename "$input_image") is tall enough to horizontally crop three times. Cropping..."
            crop_image_into_horizontal_thirds "$input_image" "$image_width" "$image_height"
        fi
    else
        info "$(basename "$input_image") aspect ratio is greater than 16:10. Resizing based on height..."
        create_image "$input_image" "$resized_desktop_image" "$super_res_image" "$image_height" 3200 4800 "x4800" 2
        if [ $? -ne 0 ]; then return 1; fi
        apply_convert_heic "$resized_desktop_image"
    fi
}

# Function to process the phone image based on aspect ratio
process_phone_image() {
    local input_image="$1"
    local super_res_image="$2"
    local resized_phone_image="$3"
    local image_width="$4"
    local image_height="$5"
    local aspect_ratio="$6"

    if (( $(echo "$aspect_ratio >= 2 / 3" | bc -l) )); then
        info "$(basename "$input_image") aspect ratio is greater than or equal to 2:3. Resizing based on height..."
        if (( $(echo "$aspect_ratio < 1" | bc -l) )); then
        # if (( $(echo "$aspect_ratio < 5 / 4" | bc -l) )); then
            echo "$(basename "$input_image") is too thin to vertically crop three times. Continuing..."
            create_image "$input_image" "$resized_phone_image" "$super_res_image" "$image_height" 3840 3840 "x3840" 2
            if [ $? -ne 0 ]; then return 1; fi
            apply_convert_heic "$resized_phone_image"
        else
            echo "$(basename "$input_image") is wide enough to vertically crop three times. Cropping..."
            crop_image_into_vertical_thirds "$input_image" "$image_width" "$image_height"
        fi
    else
        info "$(basename "$input_image") aspect ratio is less than 2:3. Resizing based on width..."
        create_image "$input_image" "$resized_phone_image" "$super_res_image" "$image_width" 2560 2560 "2560x" 1
        if [ $? -ne 0 ]; then return 1; fi
        apply_convert_heic "$resized_phone_image"
    fi
}

# Process a single image
process_image() {
    local process_type="${1:-both}"  # Default to "both" if no parameter is provided
    local input_image="$2"
    local basename=$(basename "$input_image" | sed 's/\.[^.]*$//')
    local super_res_image="$PROCESSING_DIR/${basename}_super_res_output.png"
    local resized_desktop_image="$DESKTOP_OUTPUT_DIR/${basename}_ml_res_desktop.png"
    local resized_phone_image="$PHONE_OUTPUT_DIR/${basename}_ml_res_phone.png"

    create_output_directory "$PROCESSING_DIR"
    create_output_directory "$ORIG_STORE_DIR"
    create_output_directory "$ORIG_ERROR_DIR"
    create_output_directory "$DESKTOP_OUTPUT_DIR"
    create_output_directory "$PHONE_OUTPUT_DIR"

    local dimensions
    dimensions=$(get_image_dimensions "$input_image")
    local image_width=${dimensions%x*}
    local image_height=${dimensions#*x}

    local aspect_ratio
    aspect_ratio=$(calculate_aspect_ratio "$image_width" "$image_height")

    if [[ "$process_type" == "d" || "$process_type" == "both" ]]; then
        process_desktop_image "$input_image" "$super_res_image" "$resized_desktop_image" "$image_width" "$image_height" "$aspect_ratio"
        if [ $? -ne 0 ]; then return 1; fi
    fi

    if [[ "$process_type" == "p" || "$process_type" == "both" ]]; then
        process_phone_image "$input_image" "$super_res_image" "$resized_phone_image" "$image_width" "$image_height" "$aspect_ratio"
        if [ $? -ne 0 ]; then return 1; fi
    fi

    success "Processing complete for $input_image."
    if [ -e "$input_image" ]; then mv "$input_image" "$ORIG_STORE_DIR"; fi
    echo
}

# Function to display usage
usage() {
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
