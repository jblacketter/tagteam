/**
 * sprites.js — Pixel Art Foundation
 *
 * SVG pixel art data + rendering functions for the Handoff Saloon characters.
 * Each character is defined as a 2D array of hex color values (null = transparent).
 * A shared renderer converts these arrays into inline SVG strings.
 */

/* global */
/* exported Sprites */
var Sprites = (function() {
  'use strict';

  // --- Color constants ---
  // Mayor
  const M_HAT   = '#2a1a0a';
  const M_BAND  = '#b8860b';
  const M_SKIN  = '#e8c86e';
  const M_EYE   = '#1a1a1a';
  const M_NOSE  = '#d4a04a';
  const M_SASH  = '#8b0000';
  const M_SUIT  = '#3a2a1a';
  const M_SHIRT = '#e8dcc8';
  const M_BOOT  = '#1a1a1a';
  const M_GOLD  = '#b8860b';

  // Rabbit
  const R_EAR   = '#d4b896';
  const R_INNER = '#e8a0a0';
  const R_BODY  = '#d4b896';
  const R_EYE   = '#1a1a1a';
  const R_NOSE  = '#e8a0a0';
  const R_APRON = '#e8dcc8';
  const R_APRB  = '#3498db';
  const R_BOOT  = '#8B7355';

  // Watcher (deputy/ranger)
  const W_HAT   = '#4a3828';
  const W_BRIM  = '#5a4838';
  const W_SKIN  = '#e8c86e';
  const W_EYE   = '#1a1a1a';
  const W_NOSE  = '#d4a04a';
  const W_VEST  = '#2d5a3a';
  const W_SHIRT = '#e8dcc8';
  const W_STAR  = '#b8860b';
  const W_PANT  = '#3a2a1a';
  const W_BOOT  = '#1a1a1a';

  // Clock
  const C_FRAME = '#8B7355';
  const C_FACE  = '#e8dcc8';
  const C_HAND  = '#1a1a1a';
  const C_PEND  = '#b8860b';

  // Saloon
  const S_WALL  = '#2a1a0a';
  const S_SHELF = '#5a3a1a';
  const S_BOTTLE= '#4a8060';
  const S_BTL2  = '#8a5030';
  const S_BTL3  = '#a08060';
  const S_COUNT = '#8B7355';
  const S_FLOOR = '#5a4020';
  const S_BOARD = '#3a2a1a';
  const _ = null; // transparent

  // --- Shared pixel-to-SVG renderer ---
  function pixelsToSVG(pixels, pixelSize, id, className, extraAttrs) {
    const h = pixels.length;
    const w = pixels[0].length;
    const svgW = w * pixelSize;
    const svgH = h * pixelSize;
    const attrs = extraAttrs || '';

    let rects = '';
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const c = pixels[y][x];
        if (c !== null) {
          rects += '<rect x="' + (x * pixelSize) + '" y="' + (y * pixelSize) +
            '" width="' + pixelSize + '" height="' + pixelSize +
            '" fill="' + c + '"/>';
        }
      }
    }

    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + svgW +
      '" height="' + svgH + '" viewBox="0 0 ' + svgW + ' ' + svgH + '"' +
      (id ? ' id="' + id + '"' : '') +
      (className ? ' class="' + className + '"' : '') +
      (attrs ? ' ' + attrs : '') +
      ' style="image-rendering:pixelated;image-rendering:crisp-edges">' +
      rects + '</svg>';
  }

  // --- Mayor pixel map (12w x 18h) ---
  function mayorPixels(state) {
    const hat = M_HAT, band = M_BAND, skin = M_SKIN, eye = M_EYE;
    const nose = M_NOSE, sash = M_SASH, suit = M_SUIT, shirt = M_SHIRT;
    const boot = M_BOOT, gold = M_GOLD;

    // State color overrides
    let suitC = suit, sashC = sash, skinC = skin, goldC = gold;
    if (state === 'approved') {
      goldC = '#27ae60'; sashC = '#27ae60';
    } else if (state === 'escalated') {
      goldC = '#e74c3c'; sashC = '#e74c3c';
    }

    return [
      [_,_,_,hat,hat,hat,hat,hat,hat,_,_,_],    // top hat top
      [_,_,hat,hat,hat,hat,hat,hat,hat,hat,_,_], // top hat mid
      [_,_,hat,hat,hat,hat,hat,hat,hat,hat,_,_], // top hat mid
      [_,band,band,band,band,band,band,band,band,band,band,_], // hat band
      [_,hat,hat,hat,hat,hat,hat,hat,hat,hat,hat,_], // hat brim
      [_,_,_,skin,skin,skin,skin,skin,skin,_,_,_],   // forehead
      [_,_,skin,eye,skin,skin,skin,skin,eye,skin,_,_], // eyes
      [_,_,skin,skin,skin,nose,nose,skin,skin,skin,_,_], // nose
      [_,_,skin,skin,goldC,goldC,goldC,goldC,skin,skin,_,_], // mustache
      [_,_,_,skin,skin,skin,skin,skin,skin,_,_,_],   // chin
      [_,_,sashC,sashC,shirt,shirt,shirt,shirt,sashC,sashC,_,_], // sash top
      [_,_,suitC,sashC,shirt,goldC,goldC,shirt,sashC,suitC,_,_], // torso+diamond
      [_,_,suitC,suitC,shirt,shirt,shirt,shirt,suitC,suitC,_,_], // torso
      [_,_,suitC,suitC,suitC,suitC,suitC,suitC,suitC,suitC,_,_], // lower torso
      [_,_,_,suitC,suitC,_,_,suitC,suitC,_,_,_],     // legs
      [_,_,_,suitC,suitC,_,_,suitC,suitC,_,_,_],     // legs
      [_,_,boot,boot,boot,_,_,boot,boot,boot,_,_],   // boots
      [_,boot,boot,boot,boot,_,_,boot,boot,boot,boot,_], // boot soles
    ];
  }

  // --- Rabbit pixel map (12w x 18h) ---
  function rabbitPixels(state) {
    const ear = R_EAR, inner = R_INNER, body = R_BODY, eye = R_EYE;
    const nose = R_NOSE, apron = R_APRON, aprb = R_APRB, boot = R_BOOT;

    let bodyC = body, earC = ear;
    if (state === 'approved') {
      bodyC = '#a8d8a8'; earC = '#a8d8a8';
    } else if (state === 'escalated') {
      bodyC = '#e8a860'; earC = '#e8a860';
    }

    return [
      [_,_,earC,earC,_,_,_,_,earC,earC,_,_],     // ear tips
      [_,earC,inner,earC,_,_,_,_,earC,inner,earC,_], // ears
      [_,earC,inner,earC,_,_,_,_,earC,inner,earC,_], // ears
      [_,_,earC,earC,earC,earC,earC,earC,earC,earC,_,_], // head top
      [_,_,bodyC,eye,bodyC,bodyC,bodyC,bodyC,eye,bodyC,_,_], // eyes
      [_,_,bodyC,bodyC,bodyC,nose,nose,bodyC,bodyC,bodyC,_,_], // nose
      [_,_,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,_,_], // mouth
      [_,_,_,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,_,_,_], // chin
      [_,_,apron,apron,apron,apron,apron,apron,apron,apron,_,_], // apron top
      [_,_,apron,apron,apron,apron,apron,apron,apron,apron,_,_], // apron
      [_,_,apron,apron,aprb,aprb,aprb,aprb,apron,apron,_,_], // apron B pocket
      [_,_,apron,apron,apron,apron,apron,apron,apron,apron,_,_], // apron bottom
      [_,_,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,_,_], // lower body
      [_,_,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,bodyC,_,_], // lower body
      [_,_,_,bodyC,bodyC,_,_,bodyC,bodyC,_,_,_],     // legs
      [_,_,_,bodyC,bodyC,_,_,bodyC,bodyC,_,_,_],     // legs
      [_,_,boot,boot,boot,_,_,boot,boot,boot,_,_],   // boots
      [_,boot,boot,boot,boot,_,_,boot,boot,boot,boot,_], // boot soles
    ];
  }

  // --- Watcher pixel map (12w x 18h) ---
  function watcherPixels(state) {
    const hat = W_HAT, brim = W_BRIM, skin = W_SKIN, eye = W_EYE;
    const nose = W_NOSE, shirt = W_SHIRT, pant = W_PANT, boot = W_BOOT;

    var vestC = W_VEST, starC = W_STAR;
    if (state === 'approved') {
      vestC = '#27ae60'; starC = '#2ecc71';
    } else if (state === 'escalated') {
      vestC = '#c0392b'; starC = '#e74c3c';
    }

    return [
      [_,_,_,hat,hat,hat,hat,hat,hat,_,_,_],
      [_,_,hat,hat,hat,hat,hat,hat,hat,hat,_,_],
      [_,brim,brim,brim,brim,brim,brim,brim,brim,brim,brim,_],
      [_,_,_,skin,skin,skin,skin,skin,skin,_,_,_],
      [_,_,skin,eye,skin,skin,skin,skin,eye,skin,_,_],
      [_,_,skin,skin,skin,nose,nose,skin,skin,skin,_,_],
      [_,_,skin,skin,skin,skin,skin,skin,skin,skin,_,_],
      [_,_,_,skin,skin,skin,skin,skin,skin,_,_,_],
      [_,_,vestC,vestC,shirt,shirt,shirt,shirt,vestC,vestC,_,_],
      [_,_,vestC,vestC,shirt,starC,starC,shirt,vestC,vestC,_,_],
      [_,_,vestC,vestC,shirt,shirt,shirt,shirt,vestC,vestC,_,_],
      [_,_,vestC,vestC,vestC,vestC,vestC,vestC,vestC,vestC,_,_],
      [_,_,_,pant,pant,pant,pant,pant,pant,_,_,_],
      [_,_,_,pant,pant,pant,pant,pant,pant,_,_,_],
      [_,_,_,pant,pant,_,_,pant,pant,_,_,_],
      [_,_,_,pant,pant,_,_,pant,pant,_,_,_],
      [_,_,boot,boot,boot,_,_,boot,boot,boot,_,_],
      [_,boot,boot,boot,boot,_,_,boot,boot,boot,boot,_],
    ];
  }

  // --- Clock pixel map (8w x 14h) ---
  function clockPixels(state) {
    const frame = C_FRAME, face = C_FACE, hand = C_HAND;
    let frameC = frame, faceC = face;

    if (state === 'working') {
      frameC = '#3498db';
    } else if (state === 'escalated') {
      frameC = '#e74c3c';
    } else if (state === 'aborted') {
      frameC = '#666'; faceC = '#999';
    }

    return [
      [_,frameC,frameC,frameC,frameC,frameC,frameC,_],  // top frame
      [frameC,frameC,faceC,faceC,faceC,faceC,frameC,frameC], // frame+face
      [frameC,faceC,faceC,faceC,faceC,faceC,faceC,frameC],  // face
      [frameC,faceC,faceC,hand,hand,faceC,faceC,frameC],     // hands (hour)
      [frameC,faceC,faceC,hand,faceC,faceC,faceC,frameC],    // hands (minute)
      [frameC,faceC,faceC,faceC,faceC,faceC,faceC,frameC],  // face
      [frameC,frameC,faceC,faceC,faceC,faceC,frameC,frameC], // frame+face
      [_,frameC,frameC,frameC,frameC,frameC,frameC,_],  // bottom frame
      [_,_,_,frameC,frameC,_,_,_],                       // stem
      [_,_,_,frameC,frameC,_,_,_],                       // stem
    ];
  }

  // --- Pendulum (separate for animation) ---
  function pendulumSVG(pixelSize) {
    const px = pixelSize;
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + (4 * px) + '" height="' + (4 * px) + '"' +
      ' class="clock-pendulum" style="image-rendering:pixelated">' +
      '<rect x="' + (1.5 * px) + '" y="0" width="' + px + '" height="' + (2.5 * px) + '" fill="' + C_PEND + '"/>' +
      '<circle cx="' + (2 * px) + '" cy="' + (3 * px) + '" r="' + (0.8 * px) + '" fill="' + C_PEND + '"/>' +
      '</svg>';
  }

  // --- Cuckoo bird (pops out on transitions) ---
  function cuckooSVG(pixelSize) {
    const px = pixelSize;
    const beak = '#e67e22';
    const bird = '#8B7355';
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + (6 * px) + '" height="' + (4 * px) + '"' +
      ' class="cuckoo-bird" style="image-rendering:pixelated">' +
      '<rect x="' + (2 * px) + '" y="0" width="' + (2 * px) + '" height="' + (2 * px) + '" fill="' + bird + '"/>' +
      '<rect x="' + (1 * px) + '" y="' + (1 * px) + '" width="' + (3 * px) + '" height="' + (2 * px) + '" fill="' + bird + '"/>' +
      '<rect x="' + (4 * px) + '" y="' + (1 * px) + '" width="' + (2 * px) + '" height="' + px + '" fill="' + beak + '"/>' +
      '<rect x="' + (2 * px) + '" y="' + (1 * px) + '" width="' + px + '" height="' + px + '" fill="#1a1a1a"/>' + // eye
      '</svg>';
  }

  // --- Saloon backdrop pixel map (simplified, 40w x 12h, rendered at larger pixel size) ---
  function backdropPixels() {
    const w = S_WALL, sh = S_SHELF, b1 = S_BOTTLE, b2 = S_BTL2, b3 = S_BTL3;
    const ct = S_COUNT, fl = S_FLOOR, bd = S_BOARD;

    // Compact backdrop — shelves, wall, counter, floor
    return [
      [w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w],
      [w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w],
      [w,w,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,w,w],
      [w,w,sh,b1,sh,b2,sh,b3,sh,b1,sh,b2,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,b3,sh,b1,sh,b2,sh,b3,sh,b1,sh,b2,sh,w,w],
      [w,w,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,sh,w,w],
      [w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w],
      [w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w,w],
      [ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct],
      [ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct,ct],
      [bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd,bd],
      [fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl],
      [fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl,fl],
    ];
  }

  // --- Portrait pixel maps (8w x 10h) for dialogue panel ---
  function mayorPortraitPixels() {
    return [
      [_,M_HAT,M_HAT,M_HAT,M_HAT,M_HAT,M_HAT,_],
      [M_BAND,M_BAND,M_BAND,M_BAND,M_BAND,M_BAND,M_BAND,M_BAND],
      [M_HAT,M_HAT,M_HAT,M_HAT,M_HAT,M_HAT,M_HAT,M_HAT],
      [_,M_SKIN,M_SKIN,M_SKIN,M_SKIN,M_SKIN,M_SKIN,_],
      [_,M_EYE,M_SKIN,M_SKIN,M_SKIN,M_SKIN,M_EYE,_],
      [_,M_SKIN,M_SKIN,M_NOSE,M_NOSE,M_SKIN,M_SKIN,_],
      [_,M_SKIN,M_GOLD,M_GOLD,M_GOLD,M_GOLD,M_SKIN,_],
      [_,_,M_SKIN,M_SKIN,M_SKIN,M_SKIN,_,_],
      [_,M_SASH,M_SHIRT,M_GOLD,M_GOLD,M_SHIRT,M_SASH,_],
      [_,M_SUIT,M_SUIT,M_SUIT,M_SUIT,M_SUIT,M_SUIT,_],
    ];
  }

  function rabbitPortraitPixels() {
    return [
      [_,R_EAR,R_EAR,_,_,R_EAR,R_EAR,_],
      [R_EAR,R_INNER,R_EAR,_,_,R_EAR,R_INNER,R_EAR],
      [_,R_EAR,R_EAR,R_BODY,R_BODY,R_EAR,R_EAR,_],
      [_,R_BODY,R_EYE,R_BODY,R_BODY,R_EYE,R_BODY,_],
      [_,R_BODY,R_BODY,R_NOSE,R_NOSE,R_BODY,R_BODY,_],
      [_,_,R_BODY,R_BODY,R_BODY,R_BODY,_,_],
      [_,R_APRON,R_APRON,R_APRON,R_APRON,R_APRON,R_APRON,_],
      [_,R_APRON,R_APRB,R_APRB,R_APRB,R_APRB,R_APRON,_],
      [_,R_APRON,R_APRON,R_APRON,R_APRON,R_APRON,R_APRON,_],
      [_,R_BOOT,R_BOOT,_,_,R_BOOT,R_BOOT,_],
    ];
  }

  function watcherPortraitPixels() {
    return [
      [_,W_HAT,W_HAT,W_HAT,W_HAT,W_HAT,W_HAT,_],
      [W_BRIM,W_BRIM,W_BRIM,W_BRIM,W_BRIM,W_BRIM,W_BRIM,W_BRIM],
      [_,W_SKIN,W_SKIN,W_SKIN,W_SKIN,W_SKIN,W_SKIN,_],
      [_,W_EYE,W_SKIN,W_SKIN,W_SKIN,W_SKIN,W_EYE,_],
      [_,W_SKIN,W_SKIN,W_NOSE,W_NOSE,W_SKIN,W_SKIN,_],
      [_,_,W_SKIN,W_SKIN,W_SKIN,W_SKIN,_,_],
      [_,W_VEST,W_SHIRT,W_SHIRT,W_SHIRT,W_SHIRT,W_VEST,_],
      [_,W_VEST,W_SHIRT,W_STAR,W_STAR,W_SHIRT,W_VEST,_],
      [_,W_VEST,W_VEST,W_VEST,W_VEST,W_VEST,W_VEST,_],
      [_,W_BOOT,W_BOOT,_,_,W_BOOT,W_BOOT,_],
    ];
  }

  // --- Public renderers ---

  function renderMayor(state) {
    return pixelsToSVG(mayorPixels(state), 4, 'mayor-sprite', 'character-sprite mayor-sprite-svg');
  }

  function renderRabbit(state) {
    return pixelsToSVG(rabbitPixels(state), 4, 'rabbit-sprite', 'character-sprite rabbit-sprite-svg');
  }

  function renderWatcher(state) {
    return pixelsToSVG(watcherPixels(state), 4, 'watcher-sprite', 'character-sprite watcher-sprite-svg');
  }

  function renderClock(state) {
    return pixelsToSVG(clockPixels(state), 5, 'clock-sprite', 'character-sprite clock-sprite-svg');
  }

  function renderPendulum() {
    return pendulumSVG(5);
  }

  function renderCuckoo() {
    return cuckooSVG(4);
  }

  function renderSaloonBackdrop() {
    return pixelsToSVG(backdropPixels(), 6, 'saloon-backdrop', 'saloon-backdrop-svg');
  }

  function renderPortrait(speaker) {
    if (speaker === 'rabbit' || speaker === 'Rabbit' || speaker === 'bartender' || speaker === 'Bartender') {
      return pixelsToSVG(rabbitPortraitPixels(), 6, '', 'portrait-svg');
    }
    if (speaker === 'watcher' || speaker === 'Watcher') {
      return pixelsToSVG(watcherPortraitPixels(), 6, '', 'portrait-svg');
    }
    // Default to mayor
    return pixelsToSVG(mayorPortraitPixels(), 6, '', 'portrait-svg');
  }

  function renderTitle() {
    // Pixel art "THE HANDOFF SALOON" sign rendered as a styled div (not pixel art)
    // Returns an HTML string for the title banner text
    return '<div class="saloon-title-text">The Handoff Saloon</div>';
  }

  // --- Public API ---
  return {
    renderMayor: renderMayor,
    renderRabbit: renderRabbit,
    renderWatcher: renderWatcher,
    renderClock: renderClock,
    renderPendulum: renderPendulum,
    renderCuckoo: renderCuckoo,
    renderSaloonBackdrop: renderSaloonBackdrop,
    renderPortrait: renderPortrait,
    renderTitle: renderTitle,
    pixelsToSVG: pixelsToSVG,
  };

})();
