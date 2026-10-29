tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
  .fromTo(S+" .grid",{opacity:0},{opacity:1,duration:.5},t+.05)
  .fromTo(S+" .metro",{strokeDashoffset:1000},{strokeDashoffset:0,duration:1.2,ease:"power1.inOut"},t+1.1)
  .fromTo(S+" .stn",{opacity:0,scale:0,transformOrigin:"center"},{opacity:1,scale:1,duration:.25,stagger:.12,ease:"back.out(3)"},t+1.9)
  .fromTo(S+" .wc",{opacity:0},{opacity:1,duration:.25,stagger:.08},t+3.0)
  .fromTo(S+" .hole",{attr:{r:900}},{attr:{r:130},duration:1.2,ease:"power2.inOut"},t+3.4)
  .fromTo(S+" .ring",{attr:{r:900},opacity:0},{attr:{r:130},opacity:1,duration:1.2,ease:"power2.inOut"},t+3.4)
  .fromTo(S+" .dim",{opacity:0},{opacity:.82,duration:.6},t+3.4)
  .fromTo(S+" .b3 span",{yPercent:110},{yPercent:0,duration:.5,ease:"expo.out"},t+4.4);
