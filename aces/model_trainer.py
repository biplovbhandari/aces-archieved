# -*- coding: utf-8 -*-

import logging
logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

import os
import datetime
import json
import pickle
from functools import partial

import numpy as np
import tensorflow as tf
from tensorflow import keras
from keras import callbacks

from aces.config import Config
from aces.metrics import Metrics
from aces.model_builder import ModelBuilder, DeSerializeInput, ReSerializeOutput, AddExtraFeatures
from aces.data_processor import DataProcessor
from aces.utils import Utils, TFUtils


class ModelTrainer:
    """
    A class for training deep learning models.

    Attributes:
        config: An object containing the configuration settings for model training.
        model_builder: An instance of ModelBuilder for building the model.
        build_model: A partial function for building the model with the specified model type.
    """
    def __init__(self, config: Config, use_seed: bool = True, seed: int = 42):
        """
        Initialize the ModelTrainer object.

        Args:
            config: An object containing the configuration settings for model training.

        Attributes:
            config: The configuration settings for model training.
            model_builder: An instance of ModelBuilder for building the model.
            build_model: A partial function for building the model with the specified model type.
        """
        self.config = config
        self.use_seed = use_seed
        self.seed = seed
        # @FIXME: This isn't producing reproducable results
        if self.use_seed:
            # producable results
            import random
            logging.info(f"Using seed: {self.seed}")
            tf.random.set_seed(self.seed)
            np.random.seed(self.seed)
            random.seed(2)

        if self.config.LOSS == "custom_focal_tversky_loss":
            self.config.LOSS = Metrics.focal_tversky_loss
            self.LOSS_TXT = Metrics.focal_tversky_loss.__func__.__name__ # "focal_tversky_loss"
        else:
            self.config.LOSS_TXT = self.config.LOSS

        self.model_builder = ModelBuilder(
            features=self.config.FEATURES,
            out_classes=self.config.OUT_CLASS_NUM,
            optimizer=self.config.OPTIMIZER,
            loss=self.config.LOSS
        )
        self.build_model = partial(self.model_builder.build_model, model_type=self.config.MODEL_TYPE,
                                   **{"FOR_AI_PLATFORM": self.config.USE_AI_PLATFORM,
                                      "DERIVE_FEATURES": self.config.DERIVE_FEATURES})

    def train_model(self) -> None:
        """
        Train the model using the provided configuration settings.

        This method performs the following steps:
        1. Configures memory growth for TensorFlow.
        2. Creates TensorFlow datasets for training, testing, and validation.
        3. Builds and compiles the model.
        4. Prepares the output directory for saving models and results.
        5. Starts the training process.
        6. Evaluates and prints validation metrics.
        7. Saves training parameters, plots, and models.
        """
        logging.info("****************************************************************************")
        logging.info("****************************** Clear Session... ****************************")
        keras.backend.clear_session()
        logging.info("****************************************************************************")
        print(f"****************************** Configure memory growth... ************************")
        physical_devices = TFUtils.configure_memory_growth()
        self.config.physical_devices = physical_devices
        logging.info("****************************************************************************")
        logging.info("****************************** creating datasets... ************************")
        self.create_datasets(print_info=True)

        if self.config.USE_AI_PLATFORM:
            if self.config.IS_DNN:
                logging.info("****************************************************************************")
                logging.info("******* building and compiling model for ai platform... ********************")
                self.build_and_compile_model_ai_platform()
        else:
            logging.info("****************************************************************************")
            logging.info("************************ building and compiling model... *******************")
            self.build_and_compile_model(print_model_summary=True)

        logging.info("****************************************************************************")
        logging.info("************************ preparing output directory... *********************")
        self.prepare_output_dir()
        logging.info("****************************************************************************")
        logging.info("****************************** training model... ***************************")
        self.start_training()

        if self.config.USE_AI_PLATFORM:
            logging.info(self.model.summary())

        logging.info("****************************************************************************")
        logging.info("****************************** evaluating model... *************************")
        self.evaluate_and_print_val()
        logging.info("****************************************************************************")
        logging.info("****************************** saving parameters... ************************")
        ModelTrainer.save_parameters(**self.config.__dict__)
        logging.info("****************************************************************************")
        logging.info("*************** saving model config and history object... ******************")
        self.save_history_object()
        if self.config.USE_AI_PLATFORM:
            ModelTrainer.save_model_config(self.config.MODEL_SAVE_DIR, **self._model.get_config())
        else:
            ModelTrainer.save_model_config(self.config.MODEL_SAVE_DIR, **self.model.get_config())
        logging.info("****************************************************************************")
        logging.info("****************************** saving plots... *****************************")
        self.save_plots()
        logging.info("****************************************************************************")
        logging.info("****************************** saving models... ****************************")
        self.save_models()
        logging.info("****************************************************************************")

    def prepare_output_dir(self) -> None:
        """
        Prepare the output directory for saving models and results.

        Creates a directory with a timestamped name and increments the version number if necessary.
        """
        today = datetime.date.today().strftime("%Y_%m_%d")
        iterator = 1
        while True:
            self.model_dir_name = f"trial_{self.config.MODEL_TYPE}_{today}_v{iterator}"
            self.config.MODEL_SAVE_DIR = self.config.OUTPUT_DIR / self.model_dir_name
            try:
                os.mkdir(self.config.MODEL_SAVE_DIR)
            except FileExistsError:
                logging.info(f"> {self.config.MODEL_SAVE_DIR} exists, creating another version...")
                iterator += 1
                continue
            break

    def create_datasets(self, print_info: bool = False) -> None:
        """
        Create TensorFlow datasets for training, testing, and validation.

        Args:
            print_info: Flag indicating whether to print dataset information.

        Prints information about the created datasets if print_info is set to True.
        """
        self.TRAINING_DATASET = DataProcessor.get_dataset(
            f"{str(self.config.TRAINING_DIR)}/*",
            self.config.FEATURES,
            self.config.LABELS,
            self.config.PATCH_SHAPE[0],
            self.config.BATCH_SIZE,
            self.config.OUT_CLASS_NUM,
            **{**self.config.__dict__, "training": True},
        )

        if self.config.DERIVE_FEATURES:
            self.TRAINING_DATASET = self.TRAINING_DATASET.map(AddExtraFeatures(self.config.ADDED_FEATURES), num_parallel_calls=tf.data.AUTOTUNE)
        
        self.TRAINING_DATASET = self.TRAINING_DATASET.batch(self.config.BATCH_SIZE).repeat()

        self.VALIDATION_DATASET = DataProcessor.get_dataset(
            f"{str(self.config.VALIDATION_DIR)}/*",
            self.config.FEATURES,
            self.config.LABELS,
            self.config.PATCH_SHAPE[0],
            1,
            self.config.OUT_CLASS_NUM,
            **self.config.__dict__,
        )

        if self.config.DERIVE_FEATURES:
            self.VALIDATION_DATASET = self.VALIDATION_DATASET.map(AddExtraFeatures(self.config.ADDED_FEATURES), num_parallel_calls=tf.data.AUTOTUNE)
        
        self.VALIDATION_DATASET = self.VALIDATION_DATASET.batch(1).repeat()

        self.TESTING_DATASET = DataProcessor.get_dataset(
            f"{str(self.config.TESTING_DIR)}/*",
            self.config.FEATURES,
            self.config.LABELS,
            self.config.PATCH_SHAPE[0],
            1,
            self.config.OUT_CLASS_NUM,
            **self.config.__dict__,
        )
        
        if self.config.DERIVE_FEATURES:
            self.TESTING_DATASET = self.TESTING_DATASET.map(AddExtraFeatures(self.config.ADDED_FEATURES), num_parallel_calls=tf.data.AUTOTUNE)
        
        self.TESTING_DATASET = self.TESTING_DATASET.batch(1)

        if print_info:
            logging.info("Printing dataset info:")
            DataProcessor.print_dataset_info(self.TRAINING_DATASET, "Training")
            DataProcessor.print_dataset_info(self.TESTING_DATASET, "Testing")
            DataProcessor.print_dataset_info(self.VALIDATION_DATASET, "Validation")

    def build_and_compile_model(self, print_model_summary: bool = True) -> None:
        """
        Build and compile the model.

        Args:
            print_model_summary: Flag indicating whether to print the model summary.

        Builds and compiles the model using the provided configuration settings.
        Prints the model summary if print_model_summary is set to True.
        """
        self.model = self.build_model(**self.config.__dict__)
        if print_model_summary:  logging.info(self.model.summary())

    def build_and_compile_model_ai_platform(self) -> None:
        """
        Build and compile the model.

        Args:
            print_model_summary: Flag indicating whether to print the model summary.

        Builds and compiles the model using the provided configuration settings.
        Prints the model summary if print_model_summary is set to True.
        """
        model, wrapped_model = self.build_model(**self.config.__dict__)
        logging.info(model.summary())
        self._model = model
        self.model = wrapped_model

    def start_training(self) -> None:
        """
        Start the training process.

        Trains the model using the provided configuration settings and callbacks.
        """
        model_checkpoint = callbacks.ModelCheckpoint(
            f"{str(self.config.MODEL_SAVE_DIR)}/{self.config.MODEL_CHECKPOINT_NAME}.tf",
            monitor=self.config.CALLBACK_PARAMETER,
            save_best_only=True,
            mode="auto",
            verbose=1,
            save_weights_only=False,
        )  # save best model

        early_stopping = callbacks.EarlyStopping(
            monitor=self.config.CALLBACK_PARAMETER,
            patience=int(0.3 * self.config.EPOCHS),
            verbose=1,
            mode="auto",
            restore_best_weights=True,
        )
        tensorboard = callbacks.TensorBoard(log_dir=str(self.config.MODEL_SAVE_DIR / "logs"), write_images=True)

        def lr_scheduler(epoch):
            if epoch < self.config.RAMPUP_EPOCHS:
                return self.config.MAX_LR
            elif epoch < self.config.RAMPUP_EPOCHS + self.config.SUSTAIN_EPOCHS:
                return self.config.MID_LR
            else:
                return self.config.MIN_LR

        lr_callback = callbacks.LearningRateScheduler(lambda epoch: lr_scheduler(epoch), verbose=True)

        model_callbacks = [model_checkpoint, tensorboard]

        if self.config.USE_ADJUSTED_LR:
            model_callbacks.append(lr_callback)
            
        if self.config.EARLY_STOPPING:
            model_callbacks.append(early_stopping)

        self.model_callbacks = model_callbacks

        self.history = self.model.fit(
            x=self.TRAINING_DATASET,
            epochs=self.config.EPOCHS,
            steps_per_epoch=(self.config.TRAIN_SIZE // self.config.BATCH_SIZE),
            validation_data=self.VALIDATION_DATASET,
            validation_steps=self.config.VAL_SIZE,
            callbacks=model_callbacks,
        )

    def evaluate_and_print_val(self) -> None:
        """
        Evaluate and print validation metrics.

        Evaluates the model on the validation dataset and prints the metrics.
        """
        logging.info("************************************************")
        logging.info("************************************************")
        logging.info("Validation")
        # Tip: You can remove steps=self.config.TEST_SIZE and match the TEST_SIZE from the env
        evaluate_results = self.model.evaluate(self.TESTING_DATASET, steps=self.config.TEST_SIZE) # , steps=self.config.TEST_SIZE
        for name, value in zip(self.model.metrics_names, evaluate_results):
            logging.info(f"{name}: {value}")
        logging.info("\n")

    @staticmethod
    def save_parameters(**config) -> None:
        """
        Save the training parameters to a text file.

        Saves the training parameters used in the configuration settings to a text file.
        """
        with open(f"{str(config.get('MODEL_SAVE_DIR'))}/parameters.txt", "w") as f:
            f.write(f"TRAIN_SIZE: {config.get('TRAIN_SIZE')}\n")
            f.write(f"TEST_SIZE: {config.get('TEST_SIZE')}\n")
            f.write(f"VAL_SIZE: {config.get('VAL_SIZE')}\n")
            f.write(f"BATCH_SIZE: {config.get('BATCH_SIZE')}\n")
            f.write(f"EPOCHS: {config.get('EPOCHS')}\n")
            f.write(f"LOSS: {config.get('LOSS_TXT')}\n")
            if config.get('USE_ADJUSTED_LR'):
                f.write(f"USE_ADJUSTED_LR: {config.get('USE_ADJUSTED_LR')}\n")
                f.write(f"MAX_LR: {config.get('MAX_LR')}\n")
                f.write(f"MID_LR: {config.get('MID_LR')}\n")
                f.write(f"MIN_LR: {config.get('MIN_LR')}\n")
                f.write(f"RAMPUP_EPOCHS: {config.get('RAMPUP_EPOCHS')}\n")
                f.write(f"SUSTAIN_EPOCHS: {config.get('SUSTAIN_EPOCHS')}\n")
            f.write(f"DROPOUT_RATE: {config.get('DROPOUT_RATE')}\n")
            f.write(f"ACTIVATION_FN: {config.get('ACTIVATION_FN')}\n")
            f.write(f"FEATURES: {config.get('FEATURES')}\n")
            f.write(f"LABELS: {config.get('LABELS')}\n")
            f.write(f"PATCH_SHAPE: {config.get('PATCH_SHAPE')}\n")
            f.write(f"CALLBACK_PARAMETER: {config.get('CALLBACK_PARAMETER')}\n")
            f.write(f"MODEL_TYPE: {config.get('MODEL_TYPE')}\n")
            f.write(f"TRANSFORM_DATA: {config.get('TRANSFORM_DATA')}\n")
            f.write(f"MODEL_NAME: {config.get('MODEL_NAME')}.h5\n")
            f.write(f"MODEL_CHECKPOINT_NAME: {config.get('MODEL_CHECKPOINT_NAME')}.h5\n")        
        f.close()

    @staticmethod
    def save_model_config(save_dir, **model_config) -> None:
        with open(f"{save_dir}/config.json", "w") as f:
            json.dump(model_config, f, indent=4)
        f.close()

    def save_plots(self) -> None:
        """
        Save plots and model visualization.

        Saves the model architecture plot, training history plot, and model object.
        """
        logging.info(f"Saving plots and model visualization at {self.config.MODEL_SAVE_DIR}...")
        keras.utils.plot_model(self.model, f"{self.config.MODEL_SAVE_DIR}/model.png", show_shapes=True)
        Utils.plot_metrics([key.replace("val_", "") for key in self.history.history.keys() if key.startswith("val_")],
                           self.history.history, len(self.history.epoch), self.config.MODEL_SAVE_DIR)

    def save_history_object(self) -> None:
        """
        Save the history object.
        """
        with open(f"{self.config.MODEL_SAVE_DIR}/model.pkl", "wb") as f:
            pickle.dump(self.history.history, f)

    def save_models(self) -> None:
        """
        Save the trained models.

        Saves the trained models in different formats: h5 and tf formats.
        """
        if self.config.USE_AI_PLATFORM:
            updated_model = self.serialize_model()
            if not issubclass(self.model.__class__, keras.Model):
                # Saving the model to HDF5 format requires the model to be a Functional model or a Sequential model
                updated_model.save(f"{str(self.config.MODEL_SAVE_DIR)}/{self.config.MODEL_NAME}.h5", save_format="h5")
            updated_model.save(f"{str(self.config.MODEL_SAVE_DIR)}/{self.config.MODEL_NAME}.tf", save_format="tf")
        else:
            if not issubclass(self.model.__class__, keras.Model):
                self.model.save(f"{str(self.config.MODEL_SAVE_DIR)}/{self.config.MODEL_NAME}.h5", save_format="h5")
            self.model.save(f"{str(self.config.MODEL_SAVE_DIR)}/{self.config.MODEL_NAME}.tf", save_format="tf")

    def serialize_model(self) -> tf.keras.Model:
        """
        Serialize and save the trained models.

        Saves the trained models in different formats: h5 and tf formats.
        """
        input_deserializer = DeSerializeInput()
        output_deserializer = ReSerializeOutput()
        if Config.DERIVE_FEATURES:
            serialized_inputs = {
                b: tf.keras.Input(shape=[], dtype="string", name=b) for b in Config.FEATURES + Config.ADDED_FEATURES
            }
        else:
            serialized_inputs = {
                b: tf.keras.Input(shape=[], dtype="string", name=b) for b in Config.FEATURES
            }
        updated_model_input = input_deserializer(serialized_inputs)
        updated_model = self.model(updated_model_input)
        updated_model = output_deserializer(updated_model)
        updated_model = tf.keras.Model(serialized_inputs, updated_model)
        keras.utils.plot_model(updated_model, f"{self.config.MODEL_SAVE_DIR}/model.png", show_shapes=True)
        return updated_model
