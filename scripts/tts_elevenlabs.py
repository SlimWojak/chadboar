#!/usr/bin/env python3
"""Text-to-speech using ElevenLabs API."""
import os
import sys
from pathlib import Path
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

def text_to_speech(text: str, output_path: str, voice_id: str = "pNInz6obpgDQGcFmaJgB"):
    """
    Convert text to speech using ElevenLabs.
    
    Args:
        text: The text to convert
        output_path: Where to save the audio file
        voice_id: ElevenLabs voice ID (default: Adam - deep baritone)
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)
    
    client = ElevenLabs(api_key=api_key)
    
    print(f"Generating speech with voice: {voice_id}", file=sys.stderr)
    
    # Generate audio
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        optimize_streaming_latency="0",
        output_format="mp3_44100_128",
        text=text,
        model_id="eleven_multilingual_v2",
        voice_settings=VoiceSettings(
            stability=0.5,
            similarity_boost=0.75,
            style=0.0,
            use_speaker_boost=True,
        ),
    )
    
    # Save audio
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "wb") as f:
        for chunk in audio:
            if chunk:
                f.write(chunk)
    
    print(f"Saved audio to: {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 tts_elevenlabs.py <text> <output_path> [voice_id]", file=sys.stderr)
        sys.exit(1)
    
    text = sys.argv[1]
    output_path = sys.argv[2]
    voice_id = sys.argv[3] if len(sys.argv) > 3 else "pNInz6obpgDQGcFmaJgB"
    
    text_to_speech(text, output_path, voice_id)
