"""The image's entry file.

The deploy chart runs the image's default command, `python main.py`, from the
image's working directory.
"""

from management_api.server import main

if __name__ == "__main__":
    main()
