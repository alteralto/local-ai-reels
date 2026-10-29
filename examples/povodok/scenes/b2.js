tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
  .fromTo(S+" .fig",{opacity:0,y:30},{opacity:1,y:0,duration:.35,stagger:.08,ease:"power3.out"},t+.3)
  .fromTo(S+" .stat span",{yPercent:110},{yPercent:0,duration:.5,ease:"expo.out"},t+3.1)
  .to(S+" .odd",{fill:"#ffd23f",duration:.25},t+3.2)
  .fromTo(S+" .note",{opacity:0},{opacity:1,duration:.35},t+3.6)
  .fromTo(S+" .house",{strokeDashoffset:700},{strokeDashoffset:0,duration:.6,ease:"power2.inOut"},t+5.8)
  .to(S+" .odd",{opacity:.55,duration:.4},t+6.2);
