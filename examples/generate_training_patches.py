# -*- coding: utf-8 -*-

"""
This script generates training data for a machine learning model.
The data is split into training, validation, and test sets using the Apache Beam library.
Earth Engine Python API is used to load and manipulate geospatial data.
The script uses TensorFlow to serialize the data into TFRecord format.
"""

import apache_beam as beam

import ee

try:
    from aces.utils import EEUtils, TFUtils, Utils
    from aces.config import Config
except ModuleNotFoundError:
    print("ModuleNotFoundError: Attempting to import from parent directory.")
    import os, sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from aces.utils import EEUtils, TFUtils, Utils
    from aces.config import Config

# Before running this script, you need to authenticate to Google Cloud:
# https://cloud.google.com/dataflow/docs/quickstarts/create-pipeline-python#before-you-begin

# ToDo: Is there a way to use seed value in the split?

__all__ = ["TrainingDataGenerator"]

class TrainingDataGenerator:
    """
    A class to generate training data for machine learning models.
    This class utilizes Apache Beam for data processing and Earth Engine for handling geospatial data.
    """
    def __init__(self, include_after: bool = False, use_service_account: bool = False):
        """
        Constructor for the TrainingDataGenerator class.
        
        Parameters:
        include_after (bool): If True, includes "after" images in the generated data. Default is False.
        """
        self.output_bucket = "bhutan-aces"
        self.kernel_size = 128
        self.grace = 10
        self.scale = Config.SCALE
        self.include_after = include_after
        self.test_ratio = 0.2
        self.validation_ratio = 0.2
        self.seed = 100
        self.use_service_account = use_service_account

    def load_data(self) -> None:
        """
        Load the necessary data from Earth Engine and prepare it for use.
        """
        EEUtils.initialize_session(use_highvolume=True, key=Config.EE_SERVICE_CREDENTIALS if self.use_service_account else None)
        self.l1 = ee.FeatureCollection("projects/servir-sco-assets/assets/Bhutan/BT_Admin_1")
        self.paro = self.l1.filter(ee.Filter.eq("ADM1_EN", "Paro"))

        self.sample_locations = ee.FeatureCollection("projects/servir-sco-assets/assets/Bhutan/ACES_2/paro_2021_all_class_samples")
        self.sample_locations = self.sample_locations.randomColumn("random", self.seed)
        self.training_sample_locations = self.sample_locations.filter(ee.Filter.gt("random", self.validation_ratio + self.test_ratio)) # > 0.4
        self.validation_sample_locations = self.sample_locations.filter(ee.Filter.lte("random", self.validation_ratio)) # <= 0.2
        self.test_sample_locations = self.sample_locations.filter(ee.Filter.And(ee.Filter.gt("random", self.validation_ratio),
                                                               ee.Filter.lte("random", self.validation_ratio + self.test_ratio))) # > 0.2 and <= 0.4
        self.sample_size = self.sample_locations.size().getInfo()
        print("Sample size:", self.sample_size)
        self.sample_locations_list = self.sample_locations.toList(self.sample_size + self.grace)

        self.label = ee.Image("projects/servir-sco-assets/assets/Bhutan/ACES_2/paro_2021_all_class_label").rename("class").unmask(0, False)
        # self.other = self.label.remap([0, 1], [1, 0]).rename(["other"])

        self.composite_after = ee.Image("projects/servir-sco-assets/assets/Bhutan/ACES_2/Paro_Rice_Composite_2021/composite_after")

        self.composite_before = ee.Image("projects/servir-sco-assets/assets/Bhutan/ACES_2/Paro_Rice_Composite_2021/composite_before")
        
        self.composite_during = ee.Image("projects/servir-sco-assets/assets/Bhutan/ACES_2/Paro_Rice_Composite_2021/composite_during")

        self.composite_before = self.composite_before.regexpRename("$(.*)", "_before")
        self.composite_after = self.composite_after.regexpRename("$(.*)", "_after")
        self.composite_during = self.composite_during.regexpRename("$(.*)", "_during")

        self.srtm = ee.Image("USGS/SRTMGL1_003")
        self.slope = ee.Algorithms.Terrain(self.srtm).select("slope")
        
        stats = EEUtils.calculate_min_max_statistics(self.srtm, self.paro, self.scale)
        self.srtm = self.srtm.unitScale(stats.get("elevation_min"), stats.get("elevation_max"))
        self.slope = self.slope.unitScale(0, 90)

        if self.include_after:
            self.image = self.composite_before.addBands(self.composite_during).addBands(self.composite_after).toFloat()
            self.image = self.image.select(Config.FEATURES)
            self.image = self.image.addBands(self.label).toFloat()
        else:
            self.image = self.composite_before.addBands(self.composite_during).toFloat()
            self.image = self.image.select(Config.FEATURES)
            self.image = self.image.addBands(self.label).toFloat()
        
        print("Image bands:", self.image.bandNames().getInfo())
        self.selectors = self.image.bandNames().getInfo()

        proj = ee.Projection("EPSG:4326").atScale(10).getInfo()

        # Get scales out of the transform.
        self.scale_x = proj["transform"][0]
        self.scale_y = -proj["transform"][4]

    def generate_training_patch_data(self) -> None:
        """
        Use Apache Beam to generate training, validation, and test patch data from the loaded data.
        """
        with beam.Pipeline(options=Config.beam_options) as pipeline:
            training_data, validation_data, test_data = (
                pipeline
                | "Create range" >> beam.Create(range(0, self.sample_size, 1))
                | "Yield sample points" >> beam.Map(EEUtils.beam_yield_sample_points, self.sample_locations_list, self.use_service_account)
                | "Get patch" >> beam.Map(EEUtils.beam_get_training_patches, self.image, self.selectors, self.scale, self.kernel_size, self.use_service_account)
                | "Filter patches" >> beam.Filter(Utils.filter_good_patches)
                | "Serialize" >> beam.Map(TFUtils.beam_serialize)
                | "Split dataset" >> beam.Partition(Utils.split_dataset, 3, validation_ratio=self.validation_ratio, test_ratio=self.test_ratio)
            )

            # Write the datasets to TFRecord files in the output bucket
            training_data | "Write training data" >> beam.io.WriteToTFRecord(
                f"gs://{self.output_bucket}/experiments_paro_{self.kernel_size}x{self.kernel_size}_before_during{'_after' if self.include_after else ''}_training/training", file_name_suffix=".tfrecord.gz"
            )
            validation_data | "Write validation data" >> beam.io.WriteToTFRecord(
                f"gs://{self.output_bucket}/experiments_paro_{self.kernel_size}x{self.kernel_size}_before_during{'_after' if self.include_after else ''}_validation/validation", file_name_suffix=".tfrecord.gz"
            )
            test_data | "Write test data" >> beam.io.WriteToTFRecord(
                f"gs://{self.output_bucket}/experiments_paro_{self.kernel_size}x{self.kernel_size}_before_during{'_after' if self.include_after else ''}_testing/testing", file_name_suffix=".tfrecord.gz"
            )

    def generate_training_patch_seed_data(self) -> None:
        """
        Use Apache Beam to generate training, validation, and test patch data from the loaded data.
        """
        def _generate_data_seed(image, data, selectors, scale, kernel_size, use_service_account, output_path) -> None:
            with beam.Pipeline(options=Config.beam_options) as pipeline:
                _ = (
                    pipeline
                    | "Create range" >> beam.Create(range(0, data.size().getInfo(), 1))
                    | "Yield sample points" >> beam.Map(EEUtils.beam_yield_sample_points, data.toList(data.size()), use_service_account)
                    | "Get patch" >> beam.Map(EEUtils.beam_get_training_patches, image, selectors, scale, kernel_size, use_service_account)
                    | "Serialize" >> beam.Map(TFUtils.beam_serialize)
                    | "Write training data" >> beam.io.WriteToTFRecord(output_path, file_name_suffix=".tfrecord.gz")
                )
        _generate_data_seed(self.image, self.training_sample_locations, self.selectors, self.scale, self.kernel_size,
                            self.use_service_account, f"gs://{self.output_bucket}/experiments_paro_seed_{self.kernel_size}x{self.kernel_size}_before_during{'_after' if self.include_after else ''}_training/training")

        _generate_data_seed(self.image, self.validation_sample_locations, self.selectors, self.scale, self.kernel_size,
                            self.use_service_account, f"gs://{self.output_bucket}/experiments_paro_seed_{self.kernel_size}x{self.kernel_size}_before_during{'_after' if self.include_after else ''}_validation/validation")
            
        _generate_data_seed(self.image, self.test_sample_locations, self.selectors, self.scale, self.kernel_size,
                            self.use_service_account, f"gs://{self.output_bucket}/experiments_paro_seed_{self.kernel_size}x{self.kernel_size}_before_during{'_after' if self.include_after else ''}_testing/testing")

    def generate_training_point_data(self) -> None:
        """
        Use Apache Beam to generate training, validation, and test point data from the loaded data.
        """
        sample_kwargs = { "properties": self.selectors, "scale": self.scale, "geometries": False }
        training_sample_points = EEUtils.sample_image_by_collection(self.image, self.training_sample_locations, **sample_kwargs)
        validation_sample_points = EEUtils.sample_image_by_collection(self.image, self.validation_sample_locations, **sample_kwargs)
        test_sample_points = EEUtils.sample_image_by_collection(self.image, self.test_sample_locations, **sample_kwargs)

        training_file_prefix = f"experiments_dnn_points_before_during{'_after' if self.include_after else ''}_training/training"
        validation_file_prefix = f"experiments_dnn_points_before_during{'_after' if self.include_after else ''}_validation/validation"
        test_file_prefix = f"experiments_dnn_points_before_during{'_after' if self.include_after else ''}_testing/testing"

        export_kwargs = { "bucket": self.output_bucket, "selectors": self.selectors }
        EEUtils.export_training_data(training_sample_points, export_type="cloud", start_training=False, **{**export_kwargs, "file_prefix": training_file_prefix, "description": "Training"})
        EEUtils.export_training_data(validation_sample_points, export_type="cloud", start_training=False, **{**export_kwargs, "file_prefix": validation_file_prefix, "description": "Validation"})
        EEUtils.export_training_data(test_sample_points, export_type="cloud", start_training=False, **{**export_kwargs, "file_prefix": test_file_prefix, "description": "Test"})

    def run_patch_generator(self) -> None:
        """
        Run the patch training data generation process.
        """
        self.load_data()
        self.generate_training_patch_data()

    def run_patch_generator_seed(self) -> None:
        """
        Run the patch training data generation process.
        """
        self.load_data()
        self.generate_training_patch_seed_data()

    def run_point_generator(self) -> None:
        """
        Run the point training data generation process.
        """
        self.load_data()
        self.generate_training_point_data()


if __name__ == "__main__":
    print("Program started..")
    generator = TrainingDataGenerator(use_service_account=True)
    # generator.run_patch_generator()
    # generator.run_patch_generator_seed()
    generator.run_point_generator()
    print("\nProgram completed.")