from PIL import Image
from agents.image_agent import generate_image
from config import FINAL_SIZE


def finalize_design(prompt: str, api_key: str) -> Image.Image:
    # Re-runs the same prompt at full resolution. The model is non-deterministic,
    # so the output won't be pixel-identical to the brainstorm preview — that's expected.
    return generate_image(prompt, api_key, size=FINAL_SIZE)
