tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
  .fromTo(S+" .grid",{opacity:0},{opacity:1,duration:.6},t+.05)
  .fromTo(S+" .wc",{opacity:0,scale:.4,transformOrigin:"250px 250px"},{opacity:1,scale:1,duration:.35,ease:"back.out(3)"},t+.4)
  .fromTo(S+" .me",{opacity:0},{opacity:1,duration:.3},t+.6)
  .fromTo(S+" .b1a span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+1.5)
  .fromTo(S+" .leash",{strokeDashoffset:290},{strokeDashoffset:0,duration:.5,ease:"power2.out"},t+2.1)
  .fromTo(S+" .b1b span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+2.15)
  .fromTo(S+" .radius",{attr:{r:0}},{attr:{r:290},duration:.9,ease:"power2.out"},t+2.7)
  .to(S+" .me",{attr:{cx:520,cy:470},duration:.25,yoyo:true,repeat:1,ease:"power1.inOut"},t+3.5);
