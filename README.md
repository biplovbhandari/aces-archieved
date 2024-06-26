## Note: This repo has been archieved and moved to this [repo](https://github.com/SERVIR/servir-aces).


# ACES (Agricultural Classification and Estimation Service)

ACES (Agricultural Classification and Estimation Service) is a Python module for generating training data and training machine learning models for remote sensing applications. It provides functionalities for data processing, data loading from Earth Engine, feature extraction, and model training.

## Features

- Data loading and processing from Earth Engine.
- Generation of training data for machine learning models.
- Training and evaluation of machine learning models (DNN, CNN, UNET).
- Support for remote sensing feature extraction.
- Integration with Apache Beam for data processing.


## Usage
Define all your configuration in `.env` file. An example of the file is provided as [`.env.example`](https://github.com/biplovbhandari/aces/blob/main/.env.example) file.

Here's an example of how to use the ACES module:

```python
from aces.config import Config
from aces.model_trainer import ModelTrainer

if __name__ == "__main__":
    config = Config()
    trainer = ModelTrainer(config)
    trainer.train_model()
```

## Contributing
Contributions to ACES are welcome! If you find any issues or have suggestions for improvements, please open an issue or submit a pull request on the GitHub repository.

## License
This project is licensed under the [GNU General Public License v3.0](https://github.com/biplovbhandari/aces/blob/main/LICENSE).
