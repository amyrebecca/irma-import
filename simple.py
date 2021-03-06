from os import path
from sys import argv
import logging

import image_operations as img
from csv_operations import write_rejects, write_manifest

from file_operations import (
    build_output, scratch_exists,
    build_scratch, get_files_by_extension, accept_tile, reject_tile,
    maybe_clean_scratch, find_scene_name)
from gis_operations import compute_coordinate_metadata
from xml_operations import parse_metadata
from config import config

logging.basicConfig(
    format='[ff-import %(name)s] %(levelname)s %(asctime)-15s %(message)s'
)

# python simple.py --clean --assemble --generate-tiles --reject --manifest --label=Before --annotate --name=20QPD input/foo

def usage():
    print """
simple.py (Simple Image Pipeline)

python simple.py [--option] SCENE_DIR

This script is used to process satellite imagery from LANDSAT 4, 5, 7, and 8
into subjects for Floating Forests. This includes sorting out tiles that only
contain land or contain too many clouds, as well as compositing the different
bands together into an RGB image and boosting certain parts of the green
channel to aid in kelp-spotting.

    --full                  Run full pipeline

    --clean                 Recreate scratch directory

    --generate              Perform all scene tile generation tasks
    --assemble              Perform color adjustment and build color output
    --generate-tiles        Build color tiles of scene

    --sort-tiles            Perform all tile sorting tasks
    --generate-mask         Regenerate mask tiles
    --remove-land           Reject tiles that are only land
    --remove-clouds         Reject tiles that are too cloudy
    --remove-all            Reject tiles that are only land or too cloudy
    --reject                Sort tiles into accepted and rejected folders

    --visualize             Show which tiles would be rejected

    --manifest              Build a manifest file for Panoptes subject upload

    --grid-size=XXX         Set custom tile size

    --land-threshhold=XX    Configure land detection
    --land-sensitivity=XX

    --cloud-threshhold=XX   Configure cloud detection
    --cloud-sensitivity=XX
    """


def parse_options():
    # note that logger is undefined when this method is active
    for arg in argv:
        if arg == "simple.py":
            continue

        if arg == "--help" or arg == "-?":
            # do nothing, we'll fall through to usage
            () #noqa

        elif arg == "--full":
            config.WITHTEMPDIR = True
            config.REBUILD = True
            config.ASSEMBLE_IMAGE = True
            config.SLICE_IMAGE = True
            config.GENERATE_MASK_TILES = True
            config.REMOVE_LAND = True
            config.REMOVE_CLOUDS = True
            config.REJECT_TILES = True
            config.BUILD_MANIFEST = True
            config.VISUALIZE_SORT = True

        elif arg == "--assemble":
            config.ASSEMBLE_IMAGE = True
        elif arg == "--generate-tiles":
            config.SLICE_IMAGE = True
        elif arg == "--generate":
            config.ASSEMBLE_IMAGE = True
            config.SLICE_IMAGE = True

        elif arg == "--clean":
            config.REBUILD = True

        elif arg == "--sort-tiles":
            config.GENERATE_MASK_TILES = True
            config.REMOVE_LAND = True
            config.REMOVE_CLOUDS = True
            config.REJECT_TILES = True
        elif arg == "--generate-mask":
            config.GENERATE_MASK_TILES = True
        elif arg == "--remove-land":
            config.REMOVE_LAND = True
        elif arg == "--remove-clouds":
            config.REMOVE_CLOUDS = True
        elif arg == "--remove-all":
            config.REMOVE_CLOUDS = True
            config.REMOVE_LAND = True
        elif arg == "--visualize":
            config.VISUALIZE_SORT = True
        elif arg == "--reject":
            config.REJECT_TILES = True
        elif arg == "--manifest":
            config.BUILD_MANIFEST = True
        elif arg == "--annotate":
            config.ANNOTATE = True

        elif arg.startswith("--grid-size="):
            config.GRID_SIZE = int(arg.split("=")[1])
        elif arg.startswith("--land-threshhold="):
            config.LAND_THRESHHOLD = int(arg.split("=")[1])
        elif arg.startswith("--land-sensitivity="):
            config.LAND_SENSITIVITY = int(arg.split("=")[1])
        elif arg.startswith("--cloud-threshhold="):
            config.CLOUD_THRESHHOLD = int(arg.split("=")[1])
        elif arg.startswith("--cloud-sensitivity="):
            config.CLOUD_SENSITIVITY = int(arg.split("=")[1])

        elif arg.startswith("--label="):
            config.LABEL = arg.split("=")[1]
        elif arg.startswith("--name="):
            config.SCENE_NAME = arg.split("=")[1]

        else:
            config.SCENE_DIR = arg

    if config.SCENE_NAME is None:
        config.SCENE_NAME = find_scene_name(config)

    config.NEW_MASK = path.join(config.SCENE_DIR, "B02.jp2")
    config.METADATA_SRC = path.join(config.SCENE_DIR, "metadata.xml")
    config.JSON_SRC = path.join(config.SCENE_DIR, "tileInfo.json")
    config.INPUT_FILE = config.NEW_MASK

def generate_mask_tiles():
    logger = logging.getLogger(config.SCENE_NAME)
    logger.info(
        "Generating mask tiles of " + str(config.GRID_SIZE) +
        "x" + str(config.GRID_SIZE) + " pixels")

    logger.info("Generating land mask tiles")
    # img.prepare_land_mask(config)

    logger.info("Generating cloud mask tiles")
    # img.prepare_cloud_mask(config)

    # generated_count = len(get_files_by_extension(path.join(config.SCRATCH_PATH, "land"), "png"))
    # logger.info("Generated " + str(generated_count) + " tiles")

def apply_rules(candidates, rejects, subdirectory, rules):
    accum = []
    logger = logging.getLogger(config.SCENE_NAME)

    logger.info("Examining " + str(len(candidates)) + " tiles for " + subdirectory)
    for filename in candidates:
        done = False
        statistics = img.get_image_statistics(path.join(
            config.SCRATCH_PATH,
            subdirectory,
            filename))
        for rule in rules:
            if not rule(*statistics):
                rejects.append(filename)
                done = True
                break
        if done:
            continue

        accum.append(filename)

    return accum

def index_to_location(filename, width, grid_size):
    per_row = width // grid_size + 1
    idx = int(filename.split("_")[1].split(".")[0])
    row = idx // per_row
    col = idx % per_row

    return [row, col]

def build_dict_for_csv(filename, reason, config):
    [width, height] = img.get_dimensions(path.join(config.SCRATCH_PATH, "scene", filename))
    [row, column] = index_to_location(filename, config.width, config.GRID_SIZE)

    my_dict = {
        '#filename1': 'after_' + filename,
        '#filename2': 'before_' + filename,
        '#reason': reason,
        '#row': row,
        '#column': column,
        '#width': width,
        '#height': height,
    }

    coordinate_metadata = compute_coordinate_metadata(row, column, width, height, config)

    my_dict.update(coordinate_metadata)
    my_dict.update(config.METADATA)
    return my_dict


def main():

    retained_tiles = []
    no_water = []
    too_cloudy = []

    parse_options()

    # if  (not config.GENERATE_MASK_TILES and
    #      not config.REJECT_TILES and
    #      not config.VISUALIZE_SORT and
    #      not config.ASSEMBLE_IMAGE and
    #      not config.SLICE_IMAGE and
    #      not config.BUILD_MANIFEST and
    #      not config.REBUILD):
    #     usage()
    #     return
    #
    # if ((config.GENERATE_MASK_TILES or
    #      config.REJECT_TILES or
    #      config.VISUALIZE_SORT or
    #      config.ASSEMBLE_IMAGE or
    #      config.BUILD_MANIFEST or
    #      config.SLICE_IMAGE) and
    #         config.SCENE_DIR == ''):
    #     usage()
    #     return
    #
    logger = logging.getLogger(config.SCENE_NAME)
    logger.setLevel(logging.INFO)
    logger.info("Processing start")

    accepts = []
    rejects = []

    [config.width, config.height] = img.get_dimensions(config.INPUT_FILE)

    if config.REBUILD or not scratch_exists(config):
        build_scratch(config)

    logger.info("Building scene tile output directory")
    build_output(config)

    metadata = parse_metadata(config.SCENE_DIR, config.METADATA_SRC, config.JSON_SRC)
    config.METADATA = metadata

    config.RED_CHANNEL = path.join(config.SCENE_DIR, "B04.jp2")
    config.GREEN_CHANNEL = path.join(config.SCENE_DIR, "B03.jp2")
    config.BLUE_CHANNEL = path.join(config.SCENE_DIR, "B02.jp2")
    config.INFRARED_CHANNEL = path.join(config.SCENE_DIR, "B03.jp2")

    if config.ASSEMBLE_IMAGE:
        logger.info("Processing source data to remove negative pixels")
        clamp = img.clamp_image

        clamp(config.RED_CHANNEL, "red", config, False)
        clamp(config.GREEN_CHANNEL, "green", config, False)
        clamp(config.BLUE_CHANNEL, "blue", config, False)

        config.RED_CHANNEL = path.join(config.SCRATCH_PATH, "red.png")
        config.GREEN_CHANNEL = path.join(config.SCRATCH_PATH, "green.png")
        config.BLUE_CHANNEL = path.join(config.SCRATCH_PATH, "blue.png")

        img.assemble_image(config)
    else:
        logger.info("Skipping scene generation")

    if config.SLICE_IMAGE:
        logger.info(
            "Generating scene tiles of " +
            str(config.GRID_SIZE) + "x" +
            str(config.GRID_SIZE)+" pixels")
        img.prepare_tiles(config)
    else:
        logger.info("Skipping scene tile generation")

    if config.REJECT_TILES:
        logger.info("Copying all tiles")
        retained_tiles = get_files_by_extension(path.join(config.SCRATCH_PATH, "scene"), "png")
        for filename in retained_tiles:
            accept_tile(filename, config)
            accepts.append(build_dict_for_csv(filename, "Accepted", config))

    if config.BUILD_MANIFEST:
        logger.info("Writing manifest")
        write_manifest(
            path.join("output", "{0}_tiles".format(config.SCENE_NAME), "manifest.csv"),
            accepts)

    if config.ANNOTATE:
        logger.info("Annotating with label {0}".format(config.LABEL))
        img.label_all(config)

    maybe_clean_scratch(config)

    logger.info("Processing finished")

if __name__ == "__main__":
    main()
