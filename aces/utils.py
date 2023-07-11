# -*- coding: utf-8 -*-

import logging
logging.basicConfig(level=logging.INFO)

import ee
import numpy as np
import matplotlib.pyplot as plt
from typing import Union, List
from aces import Config


__all__ = ["EEUtils", "TFUtils", "Utils"]


class EEUtils:
    """
    EEUtils: Earth Engine Utility Class

    This class provides utility functions to handle Earth Engine API information and make authenticated requests.
    """
    @staticmethod
    def get_credentials_by_service_account_key(key):
        """
        Helper function to retrieve credentials using a service account key.

        Parameters:
        key (str): The path to the service account key JSON file.

        Returns:
        ee.ServiceAccountCredentials: The authenticated credentials.
        """
        import json
        service_account = json.load(open(key))
        credentials = ee.ServiceAccountCredentials(service_account["client_email"], key)
        return credentials

    @staticmethod
    def initialize_session(use_highvolume : bool = False, key : Union[str, None] = None):
        """
        Initialize the Earth Engine session.

        Parameters:
        use_highvolume (bool): Whether to use the high-volume Earth Engine API.
        key (str or None): The path to the service account key JSON file. If None, the default credentials will be used.
        """
        if key is None:
            if use_highvolume:
                ee.Initialize(opt_url="https://earthengine-highvolume.googleapis.com")
            else:
                ee.Initialize()
        else:
            credentials = EEUtils.get_credentials_by_service_account_key(key)
            if use_highvolume:
                ee.Initialize(credentials, opt_url="https://earthengine-highvolume.googleapis.com")
            else:
                ee.Initialize(credentials)

    @staticmethod
    def calculate_min_max_statistics(image: ee.Image, geometry: ee.FeatureCollection, scale: int = 30) -> ee.Dictionary:
        """
        Calculate min and max of an image over a specific region.

        Parameters:
        image (ee.Image): The image to calculate statistics on.
        geometry (ee.FeatureCollection): The region to calculate statistics over.
        scale (int, optional): The scale, in meters, of the projection to compute statistics in. Default is 30.

        Returns:
        ee.Dictionary: A dictionary containing the min and max of the image.
        """
        reducers = ee.Reducer.mean() \
            .combine(reducer2=ee.Reducer.min(), sharedInputs=True) \
            .combine(reducer2=ee.Reducer.max(), sharedInputs=True)

        stats = image.reduceRegion(
            reducer=reducers,
            geometry=geometry,
            scale=scale,
            maxPixels=1E13
        )

        return stats

    @staticmethod
    def export_collection_data(collection: ee.FeatureCollection, export_type: str="cloud", start_training=True, **params) -> None:
        if export_type == "cloud":
            EEUtils._export_collection_to_cloud_storage(collection, start_training, **params)
        if export_type == "asset":
            EEUtils._export_collection_to_asset(collection, start_training, **params)
        else:
            raise NotImplementedError("Only cloud export is currently supported.")


    @staticmethod
    def _export_collection_to_asset(collection, start_training, **kwargs) -> None:
        asset_id = kwargs.get("asset_id", "myAssetId")
        logging.info(f"Exporting training data to {asset_id}..")
        training_task = ee.batch.Export.table.toAsset(
            collection=collection,
            description=kwargs.get("description", "myExportTableTask"),
            assetId=asset_id,
            selectors=kwargs.get("selectors", collection.first().propertyNames().getInfo()),
        )
        if start_training: training_task.start()

    @staticmethod
    def _export_collection_to_cloud_storage(collection, start_training, **kwargs) -> None:
        description = kwargs.get("description", "myExportTableTask")
        logging.info(f"Exporting training data to {description}..")
        training_task = ee.batch.Export.table.toCloudStorage(
            collection=collection,
            description=description,
            fileNamePrefix=kwargs.get("file_prefix") if kwargs.get("file_prefix") is not None else description,
            bucket=kwargs.get("bucket", "myBucket"),
            fileFormat=kwargs.get("file_format", "TFRecord"),
            selectors=kwargs.get("selectors", collection.first().propertyNames().getInfo()),
        )
        if start_training: training_task.start()

    @staticmethod
    def export_image(image: ee.Image, export_type: str="asset", start_training=True, **params) -> None:
        if export_type == "asset":
            EEUtils._export_image_to_asset(image, start_training, **params)
        else:
            raise NotImplementedError("Only cloud export is currently supported.")

    @staticmethod
    def _export_image_to_asset(image, start_training, **kwargs) -> None:
        asset_id = kwargs.get("asset_id", "")
        logging.info(f"Exporting image to {asset_id}..")

        training_task = ee.batch.Export.image.toAsset(
            image=image,
            description=kwargs.get("description", "myExportImageTask"),
            assetId=asset_id,
            region=kwargs.get("region", None),
            scale=kwargs.get("scale", 30),
            maxPixels=kwargs.get("max_pixels", 1E13),
        )
        if start_training: training_task.start()

    @staticmethod
    def country_bbox(country_name, max_error=100):
        """Function to get a bounding box geometry of a country

        args:
            country_name (str): US-recognized country name
            max_error (float,optional): The maximum amount of error tolerated when
                performing any necessary reprojection. default = 100

        returns:
            ee.Geometry: geometry of country bounding box
        """

        all_countries = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        return all_countries.filter(ee.Filter.eq("country_na", country_name))\
                            .geometry(max_error).bounds(max_error)

    @staticmethod
    def get_image_collection_statistics(image_collection: ee.ImageCollection) -> ee.Image:
        reducers = ee.Reducer.mean() \
            .combine(reducer2=ee.Reducer.min(), sharedInputs=True) \
                .combine(reducer2=ee.Reducer.max(), sharedInputs=True) \
                    .combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True) \
                        .combine(reducer2=ee.Reducer.percentile([25, 50, 75], ["Q1", "Q2", "Q3"]), sharedInputs=True)
        reducer = image_collection.reduce(reducer=reducers)
        return reducer.float()

    @staticmethod
    def calculate_planet_indices(image: ee.Image) -> ee.Image:
        ndvi = image.normalizedDifference(["N", "R"]).rename("NDVI")
        evi = image.expression (
            "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))", {
                "NIR": image.select("N"),
                "RED": image.select("R"),
                "BLUE": image.select("B")
            }).rename("EVI")
        ndwi = image.normalizedDifference(["G", "N"]).rename("NDWI")
        savi = image.expression("((NIR - RED) / (NIR + RED + 0.5))*(1.5)", {
            "NIR": image.select("N"),
            "RED": image.select("R")
        }).rename("SAVI")
        msavi2 = image.expression("(( (2*NIR + 1) - sqrt( ((2*NIR + 1) * (2*NIR + 1)) - 8 * (NIR - R) ) )) / 2", {
            "NIR": image.select("N"),
            "R": image.select("R")
        }).rename("MSAVI2")

        mtvi2 = image.expression("( 1.5*(1.2*(NIR - GREEN) - 2.5*(RED - GREEN)) ) / ( sqrt( ((2*NIR + 1) * (2*NIR + 1)) - (6*NIR - 5*sqrt(RED)) - 0.5 ) )", {
            "NIR": image.select("N"),
            "RED": image.select("R"),
            "GREEN": image.select("G"),
        }).rename("MTVI2")

        vari = image.expression("(GREEN - RED) / (GREEN + RED - BLUE)", {
            "GREEN": image.select("G"),
            "RED": image.select("R"),
            "BLUE": image.select("B"),
        }).rename("VARI")

        tgi = image.expression("( (120*(RED - BLUE)) - (190*(RED - GREEN)) ) / 2", {
            "GREEN": image.select("G"),
            "RED": image.select("R"),
            "BLUE": image.select("B"),
        }).rename("TGI")

        return ndvi.addBands([ndwi, savi, msavi2, mtvi2, vari, tgi]).float()

    @staticmethod
    def generate_stratified_samples(image: ee.Image, region: ee.Geometry, numPoints: int = 500, classBand: str = None, scale: int=30, seed: int=Config.SEED) -> ee.FeatureCollection:
        # Add a latitude and longitude band.
        return image.addBands(ee.Image.pixelLonLat()).stratifiedSample(
            numPoints=numPoints,
            classBand=classBand if classBand else "label",
            scale=scale,
            region=region,
            seed=seed,
        ).map(lambda f: f.setGeometry(ee.Geometry.Point([f.get("longitude"), f.get("latitude")])))

    @staticmethod
    def sample_image_by_collection(image: ee.Image, collection: ee.FeatureCollection, **kwargs: dict) -> ee.FeatureCollection:
        return image.sampleRegions(
            collection=collection,
            properties=kwargs.get("properties", collection.first().propertyNames().getInfo()),
            scale=kwargs.get("scale", None),
            geometries=kwargs.get("geometries", False),
            tileScale=kwargs.get("tile_scale", 1),
        )

    @staticmethod
    def beam_yield_sample_points(index, sample_locations: ee.List, use_service_account: bool = False) -> List:
        from aces.utils import EEUtils
        from aces.config import Config
        import ee
        EEUtils.initialize_session(use_highvolume=True, key=Config.EE_SERVICE_CREDENTIALS if use_service_account else None)
        print(f"Yielding Index: {index} of {sample_locations.size().getInfo() - 1}")
        point = ee.Feature(sample_locations.get(index)).geometry().getInfo()
        return point["coordinates"]

    @staticmethod
    def beam_get_training_patches(coords: List[float], image: ee.Image, bands: List[str] = [],
                             scale: int = 5, patch_size: int = 128, use_service_account: bool = False) -> np.ndarray:
        """Get a training patch centered on the coordinates."""
        from aces.utils import EEUtils
        from aces.config import Config
        import ee
        EEUtils.initialize_session(use_highvolume=True, key=Config.EE_SERVICE_CREDENTIALS if use_service_account else None)
        from google.api_core import exceptions, retry
        import requests
        import numpy as np
        from typing import List
        import io

        @retry.Retry(timeout=300)
        def get_patch(image: ee.Image, region: ee.Geometry, bands: List[str], patch_size: int) -> np.ndarray:
            """Get the patch of pixels in the geometry as a Numpy array."""
            # Create the URL to download the band values of the patch of pixels.
            url = image.getDownloadURL({
                "region": region,
                "dimensions": [patch_size, patch_size],
                "format": "NPY",
                "bands": bands,
            })
            # Download the pixel data. If we get "429: Too Many Requests" errors,
            # it"s safe to retry the request.
            response = requests.get(url)
            if response.status_code == 429:
                # The retry.Retry library only works with `google.api_core` exceptions.
                raise exceptions.TooManyRequests(response.text)
                # Still raise any other exceptions to make sure we got valid data.
            response.raise_for_status()

            # Load the NumPy file data and return it as a NumPy array.
            return np.load(io.BytesIO(response.content), allow_pickle=True)

        @retry.Retry()
        def compute_pixel(image: ee.Image, region: ee.Geometry, bands: List[str], patch_size: int, scale_x: float, scale_y: float) -> np.ndarray:
            """Get the patch of pixels in the geometry as a Numpy array."""

            # Make a request object.
            request = {
                "expression": image,
                "fileFormat": "NPY",
                "bandIds": bands,
                "grid": {
                    "dimensions": {
                        "width": patch_size,
                        "height": patch_size
                    },
                    "affineTransform": {
                        "scaleX": scale_x,
                        "shearX": 0,
                        "translateX": coords[0],
                        "shearY": 0,
                        "scaleY": scale_y,
                        "translateY": coords[1]
                    },
                    "crsCode": "EPSG:4326",
                },
            }
            response = ee.data.computePixels(request)
            # Load the NumPy file data and return it as a NumPy array.
            return np.load(io.BytesIO(response.content), allow_pickle=True)

        point = ee.Geometry.Point(coords)
        region = point.buffer(scale * patch_size / 2, 1).bounds(1)
        return get_patch(image, region, bands, patch_size)


class TFUtils:
    @staticmethod
    def beam_serialize(patch: np.ndarray) -> bytes:
        import tensorflow as tf

        features = {
            name: tf.train.Feature(
                float_list=tf.train.FloatList(value=patch[name].flatten())
            )
            for name in patch.dtype.names
        }
        example = tf.train.Example(features=tf.train.Features(feature=features))
        return example.SerializeToString()


class Utils:
    """
    Utils: Utility Functions for ACES

    This class provides utility functions for plotting, splitting data.
    """
    @staticmethod
    def split_dataset(element, num_partitions: int, validation_ratio: float = 0.2, test_ratio: float = 0.2) -> int:
        import random
        weights = [1 - validation_ratio - test_ratio, validation_ratio, test_ratio]
        return random.choices([0, 1, 2], weights)[0]

    @staticmethod
    def plot_metrics(metrics, history, epoch, model_save_dir):
        """
        Plot the training and validation metrics over epochs.

        Args:
            metrics: List of metrics to plot.
            history: Training history containing metric values.
            epoch: Number of epochs.
            model_save_dir: Directory to save the plot.

        Returns:
            None.
        """
        fig, ax = plt.subplots(nrows=len(metrics), sharex=True, figsize=(15, len(metrics) * 6))
        colors = ["#1f77b4", "#ff7f0e", "red", "green", "purple", "orange", "brown", "pink", "gray", "olive", "cyan"]
        for i, metric in enumerate(metrics):
            try:
                ax[i].plot(history[metric], color=colors[i], label=f"Training {metric.upper()}")
                ax[i].plot(history[f"val_{metric}"], linestyle=":", marker="o", markersize=3, color=colors[i], label=f"Validation {metric.upper()}")
                ax[i].set_ylabel(metric.upper())
                ax[i].legend()
            except Exception as e:
                logging.info(f"Exception: {e}")
                logging.info(f"Skipping {metric}.")
                continue

        ax[i].set_xticks(range(1, epoch + 1, 4))
        ax[i].set_xticklabels(range(1, epoch + 1, 4))
        ax[i].set_xlabel("Epoch")
        fig.savefig(f"{model_save_dir}/training.png", dpi=1000)

    @staticmethod
    def filter_good_patches(patch):
        """
        Filter patches to remove those with NaN or infinite values.

        Parameters:
        patch (np.ndarray): The patch to filter.

        Returns:
        bool: True if the patch has no NaN or infinite values, False otherwise.
        """
        # the getdownload url has field names so we"re using view here
        has_nan = np.isnan(np.sum(patch.view(np.float32)))
        has_inf = np.isinf(np.sum(patch.view(np.float32)))
        if has_nan or has_inf:
            return False
        return True