"""Щелчок замка из v1: два коротких транзиента (язычок + упор) без резких пиков → lock.wav.
По тому же принципу собраны брызги и штамп в v2: фильтрованный шум × экспоненциальная огибающая.
"""
import numpy as np
import scipy.io.wavfile as w
from scipy.signal import butter, lfilter

sr = 48000
n = int(.35 * sr)
out = np.zeros(n)
rng = np.random.default_rng(3)


def tick(t0, f, amp, dec):
    i = int(t0 * sr)
    m = int(.06 * sr)
    t = np.arange(m) / sr
    env = np.exp(-t / dec)
    noise = lfilter(*butter(2, [f * .6 / (sr / 2), min(f * 1.8 / (sr / 2), .99)], 'band'), rng.standard_normal(m))
    out[i:i + m] += amp * env * (0.6 * noise + 0.4 * np.sin(2 * np.pi * f * t))


tick(0.0, 2400, .5, .006)
tick(0.055, 1300, .7, .012)
out /= np.abs(out).max()
out *= .5
w.write("lock.wav", sr, np.stack([out, out], 1).astype(np.float32))
print("lock ok")
