"""Мастер-озвучка: дубли F5-TTS для каждого голоса и seed + сверка Whisper → _сверка.json.

python synth.py [голос …]      по умолчанию: denis dmitri
Для каждого голоса нужен эталон ref-<голос>-p.wav. Текст эталона и сценарий берутся из master-acc.json
({"master": "...", "ref": "..."}, ударения уже размечены «+»).
"""
import difflib, json, os, re, sys
from pathlib import Path
os.environ.setdefault("WANDB_MODE","disabled")
from f5_tts.api import F5TTS
from faster_whisper import WhisperModel
M=Path(os.environ.get("TTS_MODELS","models"))  # f5-ru/… — веса Misha24-10/F5-TTS_RUSSIAN
VOICES=sys.argv[1:] or ["denis","dmitri"]
acc=json.load(open("master-acc.json",encoding="utf-8"))
text, ref_text = acc["master"], acc["ref"]
tts=F5TTS(model="F5TTS_v1_Base",ckpt_file=str(M/"f5-ru/F5TTS_v1_Base_v2/model_last_inference.safetensors"),
          vocab_file=str(M/"f5-ru/F5TTS_v1_Base/vocab.txt"),device="cuda",hf_cache_dir=str(M/"hf"))
asr=WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo",device="cuda",compute_type="float16")
W=lambda t: re.sub(r"[^а-яa-z ]"," ",t.replace("+","").lower().replace("ё","е")).split()
res=[]
for v in VOICES:
    for seed in (7,11,31):
        f=f"m-{v}-{seed}.wav"
        tts.infer(f"ref-{v}-p.wav",ref_text,text,file_wave=f,speed=1.0,nfe_step=32,seed=seed,show_info=lambda *a:None)
        segs,_=asr.transcribe(f,language="ru")
        heard=" ".join(s.text for s in segs)
        r=difflib.SequenceMatcher(None,W(text),W(heard)).ratio()
        res.append((round(r,3),f,heard)); print(round(r,3),f,heard,flush=True)
json.dump(sorted(res,reverse=True),open("_сверка.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
