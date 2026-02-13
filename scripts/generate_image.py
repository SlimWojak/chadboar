#!/usr/bin/env python3
"""Quick image generation script using Replicate API."""
import os
import sys
import replicate
from pathlib import Path

def generate_image(prompt: str, output_path: str):
    """Generate an image using Replicate's FLUX model."""
    api_token = os.getenv("REPLICATE_API_TOKEN")
    if not api_token:
        print("ERROR: REPLICATE_API_TOKEN not set in environment", file=sys.stderr)
        sys.exit(1)
    
    client = replicate.Client(api_token=api_token)
    
    print(f"Generating image with prompt: {prompt}", file=sys.stderr)
    
    # Using FLUX.1 schnell for fast generation
    output = client.run(
        "black-forest-labs/flux-schnell",
        input={
            "prompt": prompt,
            "num_outputs": 1,
            "aspect_ratio": "1:1",
            "output_format": "png",
            "output_quality": 90
        }
    )
    
    # Download the image
    if output and len(output) > 0:
        image_file = output[0]
        print(f"Image generated: {image_file}", file=sys.stderr)
        
        # Read the file content from Replicate's FileOutput object
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Download using httpx with the URL string
        import httpx
        image_url = str(image_file.url) if hasattr(image_file, 'url') else str(image_file)
        response = httpx.get(image_url)
        response.raise_for_status()
        
        Path(output_path).write_bytes(response.content)
        
        print(f"Saved to: {output_path}")
    else:
        print("ERROR: No output generated", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 generate_image.py <prompt> <output_path>", file=sys.stderr)
        sys.exit(1)
    
    prompt = sys.argv[1]
    output_path = sys.argv[2]
    
    generate_image(prompt, output_path)
