tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
  .fromTo(S+" .grid",{opacity:0},{opacity:1,duration:.4},t+.05)
  .fromTo(S+" .zone",{opacity:0,scale:.92,transformOrigin:"center"},{opacity:1,scale:1,duration:.4,stagger:.07},t+.15)
  .fromTo(S+" .b4a span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+.3)
  .fromTo(S+" .lr",{opacity:0,attr:{r:0}},{opacity:1,attr:{r:150},duration:.6,stagger:.1,ease:"back.out(1.6)"},t+1.5)
  .fromTo(S+" .b4b span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+1.85)
  .fromTo(S+" .cta",{opacity:0,y:30},{opacity:1,y:0,duration:.45,ease:"power3.out"},t+3.0);
