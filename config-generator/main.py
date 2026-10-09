"""The image's entry file.

The deploy chart runs `python main.py generate` and `python main.py serve` as
container commands, from the image's working directory.
"""

from config_generator.cli import main

if __name__ == "__main__":
    main()
