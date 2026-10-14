import torch, numpy as np, scipy.io.wavfile as wf
from transformers import AutoProcessor, MusicgenForConditionalGeneration
p=AutoProcessor.from_pretrained("facebook/musicgen-small")
m=MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small").to("cuda")
prompts=["minimal thoughtful piano with soft synth pulse, urban documentary, calm tension, 80 bpm, no drums",
         "ambient cinematic underscore, soft piano motif, warm pad, slow, reflective city at night"]
sr=m.config.audio_encoder.sampling_rate
for i,pr in enumerate(prompts):
    x=p(text=[pr],padding=True,return_tensors="pt").to("cuda")
    torch.manual_seed(5+i)
    a=m.generate(**x,do_sample=True,guidance_scale=3,max_new_tokens=1350)  # ~27 s
    wf.write(f"music-{i}.wav",sr,a[0,0].cpu().numpy().astype(np.float32)); print("ok",i,flush=True)
