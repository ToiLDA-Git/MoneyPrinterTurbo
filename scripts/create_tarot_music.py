"""Create an original, deterministic ambient bed using synthesized tones only."""
from pathlib import Path
import wave

import numpy as np


def create(output: Path):
    sample_rate, duration = 44100, 65
    timeline = np.arange(sample_rate * duration) / sample_rate
    stereo = np.zeros((len(timeline), 2), dtype=np.float64)
    # Slowly overlapping A-minor / F-major / C-major / G-major pads.
    chords = [(220, 261.6256, 329.6276), (174.6141, 220, 261.6256),
              (130.8128, 164.8138, 195.9977), (195.9977, 246.9417, 293.6648)]
    for index in range(9):
        local = timeline - index * 8
        envelope = np.sin(np.pi * np.clip(local / 16, 0, 1)) ** 2
        for note, frequency in enumerate(chords[index % len(chords)]):
            for channel in range(2):
                tone = np.sin(2 * np.pi * frequency * (1 + channel * .0008) * local + note)
                tone += .12 * np.sin(2 * np.pi * frequency * 2 * local)
                stereo[:, channel] += envelope * tone / 12
    fade = np.minimum(np.clip(timeline / 4, 0, 1), np.clip((duration - timeline) / 5, 0, 1))
    stereo *= fade[:, None]
    stereo *= .45 / max(np.max(np.abs(stereo)), .001)
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes((stereo * 32767).astype('<i2').tobytes())
    return output


if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'tarot-assets/music/tarot-ambient.wav'
    print(create(target))
