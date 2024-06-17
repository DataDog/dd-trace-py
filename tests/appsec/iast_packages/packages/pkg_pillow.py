"""
Pillow==9.1.0

https://pypi.org/project/Pillow/
"""
import os

from flask import Blueprint
from flask import request
from flask import send_file

from .utils import ResultResponse


pkg_pillow = Blueprint("package_pillow", __name__)


@pkg_pillow.route("/pillow")
def pkg_pillow_view():
    from PIL import Image
    from PIL import ImageDraw
    from PIL import ImageFont

    response = ResultResponse(request.args.get("package_param"))

    try:
        text = request.args.get("package_param", "Hello, World!")
        img_path = "example.png"

        try:
            # Create an image with the specified text
            img = Image.new("RGB", (200, 100), color=(73, 109, 137))
            d = ImageDraw.Draw(img)
            fnt = ImageFont.load_default()
            d.text((10, 40), text, font=fnt, fill=(255, 255, 0))

            # Save the image to a file
            img.save(img_path)

            # Prepare the response to send the file
            result_output = send_file(img_path, mimetype="image/png")
        except Exception as e:
            result_output = f"Error: {str(e)}"
        finally:
            # Clean up the created image file
            if os.path.exists(img_path):
                os.remove(img_path)

        if result_output:
            response.result1 = "Image correctly generated"
        else:
            response.result1 = "Ups, image not generated"
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
