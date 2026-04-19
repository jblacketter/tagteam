/**
 * sprites.js — Flat SVG Illustration Foundation
 *
 * Hand-authored vector characters for the Handoff Saloon.
 * Each character is an inline SVG built from paths, circles, and rects.
 * Night-saloon palette (indigo / teal / silver-cyan / cream).
 */

/* global */
/* exported Sprites */
var Sprites = (function() {
  'use strict';

  // --- Night Saloon palette ---
  const SKIN       = '#d8c5a8';
  const SKIN_HI    = '#f0e1c9';
  const SKIN_SHADE = '#b89880';
  const NECK       = '#c2ae90';
  const EYE        = '#0d131f';
  const EYE_WHITE  = '#e6eaf2';
  const BROW       = '#4a5570';

  const HAT        = '#1a2038';
  const HAT_HI     = '#2d3553';
  const HAT_BAND   = '#a9c2de';
  const HAT_BAND_HI = '#c2d8ef';

  const SUIT       = '#2a3248';
  const SUIT_HI    = '#3a445e';
  const SUIT_SH    = '#1a2038';
  const SHIRT      = '#e6eaf2';
  const SHIRT_SH   = '#b4b9c7';

  const SASH       = '#2d6b6e';
  const SASH_HI    = '#3d8b8e';
  const MEDAL      = '#c2d8ef';
  const MEDAL_MID  = '#9fb8d8';
  const MEDAL_SH   = '#5a7299';

  const BOOT       = '#0d131f';
  const GROUND_SHADOW = '#000';

  // Rabbit
  const RABBIT_BODY = '#d0cfe0';
  const RABBIT_HI   = '#e8e6f0';
  const RABBIT_SH   = '#a8a7b8';
  const RABBIT_INNER = '#c78fa5';
  const APRON       = '#e6eaf2';
  const APRON_SH    = '#b4b9c7';
  const RABBIT_BOOT = '#3a3e52';

  // Watcher
  const VEST        = '#2f4a5f';
  const VEST_HI     = '#406783';
  const BRIM        = '#3d4764';
  const BANDANA     = '#d85a85';
  const STAR        = '#c0ccd9';
  const STAR_MID    = '#9fb8d8';

  // Clock
  const CLOCK_FRAME  = '#6b7890';
  const CLOCK_HI     = '#8c9bb3';
  const CLOCK_SH     = '#4a5568';
  const CLOCK_FACE   = '#e6eaf2';
  const CLOCK_HAND   = '#0d131f';
  const CLOCK_BRASS  = '#9fb8d8';

  // State-tinted colors for approved/escalated swaps
  function stateTint(baseKey, state) {
    // baseKey: 'accent' | 'sash' | 'sashH' | 'vest' | 'vestH' | 'star' | 'frame' | 'frameH' | 'rabbitBody' | 'rabbitHi' | 'aprb'
    const normal = {
      accent: MEDAL, sash: SASH, sashH: SASH_HI,
      vest: VEST, vestH: VEST_HI, star: STAR,
      frame: CLOCK_FRAME, frameH: CLOCK_HI,
      rabbitBody: RABBIT_BODY, rabbitHi: RABBIT_HI, aprb: SASH,
    };
    const approved = {
      accent: '#4bd1a3', sash: '#2b8672', sashH: '#3aaf8f',
      vest: '#2b8672', vestH: '#3aaf8f', star: '#c6e5d6',
      frame: '#3aaf8f', frameH: '#5fc7a7',
      rabbitBody: '#c6e5d6', rabbitHi: '#dcf0e5', aprb: '#3aaf8f',
    };
    const escalated = {
      accent: '#ff7fa8', sash: '#9b3a5f', sashH: '#d85a85',
      vest: '#9b3a5f', vestH: '#d85a85', star: '#f0d1d8',
      frame: '#d85a85', frameH: '#ff7fa8',
      rabbitBody: '#e8b5c0', rabbitHi: '#f0d1d8', aprb: '#d85a85',
    };
    const map = state === 'approved' ? approved : state === 'escalated' ? escalated : normal;
    return map[baseKey];
  }

  // --- SVG wrapper helper ---
  function svgWrap(viewBox, idAttr, className, body) {
    const id = idAttr ? ' id="' + idAttr + '"' : '';
    const cls = className ? ' class="' + className + '"' : '';
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + viewBox + '"' +
      id + cls + '>' + body + '</svg>';
  }

  // =========================================================================
  // MAYOR — full body
  // =========================================================================
  function mayorSVG(state) {
    const sash = stateTint('sash', state);
    const sashH = stateTint('sashH', state);
    const accent = stateTint('accent', state);
    return (
      // Ground shadow
      '<ellipse cx="60" cy="176" rx="30" ry="3" fill="' + GROUND_SHADOW + '" opacity="0.35"/>' +
      // Legs
      '<path d="M52 132 L50 168 L58 168 L60 132Z" fill="' + SUIT + '"/>' +
      '<path d="M60 132 L62 168 L70 168 L68 132Z" fill="' + SUIT_SH + '"/>' +
      // Boots
      '<path d="M42 166 L62 166 Q63 172 60 176 L40 176 Q38 171 42 166Z" fill="' + BOOT + '"/>' +
      '<path d="M58 166 L78 166 Q82 171 80 176 L60 176 Q57 172 58 166Z" fill="' + BOOT + '"/>' +
      // Jacket body (rounded coat)
      '<path d="M25 82 Q22 78 28 74 Q40 67 48 66 L72 66 Q80 67 92 74 Q98 78 95 82 L99 143 Q60 152 21 143Z" fill="' + SUIT + '"/>' +
      // Jacket right-side shade
      '<path d="M74 72 Q85 74 93 80 L97 142 Q86 146 78 144 L78 72Z" fill="' + SUIT_SH + '" opacity="0.55"/>' +
      // Shirt
      '<path d="M50 67 L50 108 L70 108 L70 67Z" fill="' + SHIRT + '"/>' +
      // Lapels
      '<path d="M48 66 L60 104 L50 74Z" fill="' + SUIT_SH + '"/>' +
      '<path d="M72 66 L60 104 L70 74Z" fill="' + SUIT_SH + '"/>' +
      // Bow tie
      '<path d="M54 72 L50 78 L54 84 L60 80 L66 84 L70 78 L66 72 L60 76Z" fill="' + sash + '"/>' +
      '<path d="M54 72 L50 78 L54 84 L60 80 L60 76Z" fill="' + sashH + '" opacity="0.7"/>' +
      '<rect x="58" y="77" width="4" height="4" fill="' + SUIT_SH + '"/>' +
      // Sash diagonal across torso
      '<path d="M20 96 L98 110 L100 118 L22 104Z" fill="' + sash + '"/>' +
      '<path d="M20 96 L98 110 L99 113 L21 99Z" fill="' + sashH + '"/>' +
      // Medallion
      '<circle cx="55" cy="103" r="5.5" fill="' + accent + '"/>' +
      '<circle cx="55" cy="103" r="3.2" fill="' + MEDAL_MID + '"/>' +
      '<path d="M52.5 100.5 L57.5 105.5 M52.5 105.5 L57.5 100.5" stroke="' + MEDAL_SH + '" stroke-width="0.7" stroke-linecap="round"/>' +
      // Arms
      '<rect x="18" y="82" width="10" height="54" rx="5" fill="' + SUIT + '"/>' +
      '<rect x="92" y="82" width="10" height="54" rx="5" fill="' + SUIT + '"/>' +
      '<rect x="19" y="84" width="3" height="48" fill="' + SUIT_HI + '"/>' +
      '<rect x="97" y="84" width="3" height="48" fill="' + SUIT_HI + '" opacity="0.5"/>' +
      // Hands
      '<circle cx="23" cy="138" r="5.5" fill="' + SKIN + '"/>' +
      '<circle cx="97" cy="138" r="5.5" fill="' + SKIN + '"/>' +
      // Neck
      '<rect x="54" y="58" width="12" height="11" fill="' + NECK + '"/>' +
      // Ears
      '<ellipse cx="39" cy="46" rx="2" ry="4" fill="' + NECK + '"/>' +
      '<ellipse cx="81" cy="46" rx="2" ry="4" fill="' + NECK + '"/>' +
      // Head
      '<ellipse cx="60" cy="45" rx="20" ry="22" fill="' + SKIN + '"/>' +
      // Head highlight (left cheek / forehead)
      '<path d="M42 42 Q46 30 56 28 Q50 40 46 54Z" fill="' + SKIN_HI + '" opacity="0.65"/>' +
      // Eyebrows
      '<path d="M47 39 L54 38" stroke="' + BROW + '" stroke-width="1.4" stroke-linecap="round"/>' +
      '<path d="M66 38 L73 39" stroke="' + BROW + '" stroke-width="1.4" stroke-linecap="round"/>' +
      // Eyes
      '<circle cx="51" cy="45.5" r="1.9" fill="' + EYE + '"/>' +
      '<circle cx="69" cy="45.5" r="1.9" fill="' + EYE + '"/>' +
      '<circle cx="51.4" cy="45" r="0.6" fill="' + EYE_WHITE + '"/>' +
      '<circle cx="69.4" cy="45" r="0.6" fill="' + EYE_WHITE + '"/>' +
      // Nose
      '<path d="M58 50 Q60 56 62 50Z" fill="' + SKIN_SHADE + '"/>' +
      // Mustache
      '<path d="M48 58 Q55 64 60 61 Q65 64 72 58 Q67 66 60 64 Q53 66 48 58Z" fill="' + MEDAL + '"/>' +
      // Hat brim (ellipse for perspective)
      '<ellipse cx="60" cy="28" rx="28" ry="4" fill="' + HAT + '"/>' +
      '<ellipse cx="60" cy="27" rx="28" ry="2" fill="' + HAT_HI + '"/>' +
      // Hat crown
      '<path d="M44 28 L44 8 Q44 4 48 4 L72 4 Q76 4 76 8 L76 28Z" fill="' + HAT + '"/>' +
      '<path d="M47 8 Q47 16 48 25" stroke="' + HAT_HI + '" stroke-width="2" fill="none" stroke-linecap="round"/>' +
      // Hat band
      '<rect x="44" y="22" width="32" height="5" fill="' + HAT_BAND + '"/>' +
      '<rect x="44" y="22" width="32" height="1.2" fill="' + HAT_BAND_HI + '"/>'
    );
  }

  // =========================================================================
  // RABBIT — full body
  // =========================================================================
  function rabbitSVG(state) {
    const body = stateTint('rabbitBody', state);
    const hi   = stateTint('rabbitHi', state);
    const aprb = stateTint('aprb', state);
    return (
      // Ground shadow
      '<ellipse cx="60" cy="176" rx="30" ry="3" fill="' + GROUND_SHADOW + '" opacity="0.35"/>' +
      // Feet
      '<ellipse cx="48" cy="170" rx="8" ry="5" fill="' + RABBIT_BOOT + '"/>' +
      '<ellipse cx="72" cy="170" rx="8" ry="5" fill="' + RABBIT_BOOT + '"/>' +
      // Back ears (behind head)
      '<path d="M44 12 Q37 18 39 48 Q42 52 46 50 L50 12Z" fill="' + body + '"/>' +
      '<path d="M76 12 Q83 18 81 48 Q78 52 74 50 L70 12Z" fill="' + body + '"/>' +
      // Inner ears
      '<path d="M46 18 Q42 22 42 44 Q45 47 47 44Z" fill="' + RABBIT_INNER + '"/>' +
      '<path d="M74 18 Q78 22 78 44 Q75 47 73 44Z" fill="' + RABBIT_INNER + '"/>' +
      // Body (apron bottom)
      '<path d="M30 112 Q28 118 30 128 L32 160 Q60 168 88 160 L90 128 Q92 118 90 112Z" fill="' + body + '"/>' +
      // Arms
      '<path d="M23 100 Q17 112 22 138 Q26 142 31 140 L33 112Z" fill="' + body + '"/>' +
      '<path d="M97 100 Q103 112 98 138 Q94 142 89 140 L87 112Z" fill="' + body + '"/>' +
      '<circle cx="27" cy="140" r="6" fill="' + hi + '"/>' +
      '<circle cx="93" cy="140" r="6" fill="' + hi + '"/>' +
      // Apron
      '<path d="M32 96 L32 158 L88 158 L88 96 Q74 86 60 86 Q46 86 32 96Z" fill="' + APRON + '"/>' +
      // Apron fold shadow
      '<path d="M34 116 L36 154 L40 154 L38 116Z" fill="' + APRON_SH + '" opacity="0.5"/>' +
      // Apron tie band across top
      '<rect x="30" y="104" width="60" height="6" fill="' + aprb + '"/>' +
      // Tie bow center
      '<path d="M54 101 L49 108 L54 114 L60 110 L66 114 L71 108 L66 101 L60 106Z" fill="' + aprb + '"/>' +
      '<rect x="58" y="106" width="4" height="4" fill="' + SUIT_SH + '" opacity="0.6"/>' +
      // Front pocket
      '<path d="M50 128 L70 128 L68 146 L52 146Z" fill="' + APRON_SH + '" opacity="0.35"/>' +
      '<path d="M50 128 L70 128 L69 131 L51 131Z" fill="' + APRON_SH + '" opacity="0.55"/>' +
      // Head
      '<circle cx="60" cy="62" r="26" fill="' + body + '"/>' +
      // Head highlight
      '<path d="M40 54 Q44 40 54 36 Q48 52 44 68Z" fill="' + hi + '" opacity="0.65"/>' +
      // Cheek blush
      '<ellipse cx="44" cy="70" rx="4" ry="2.5" fill="' + RABBIT_INNER + '" opacity="0.4"/>' +
      '<ellipse cx="76" cy="70" rx="4" ry="2.5" fill="' + RABBIT_INNER + '" opacity="0.4"/>' +
      // Eyes
      '<circle cx="51" cy="60" r="2.6" fill="' + EYE + '"/>' +
      '<circle cx="69" cy="60" r="2.6" fill="' + EYE + '"/>' +
      '<circle cx="52" cy="59.3" r="0.9" fill="' + EYE_WHITE + '"/>' +
      '<circle cx="70" cy="59.3" r="0.9" fill="' + EYE_WHITE + '"/>' +
      // Nose
      '<path d="M57 68 Q60 73 63 68 Q60 71 57 68Z" fill="' + RABBIT_INNER + '"/>' +
      // Mouth
      '<path d="M60 70 L60 74" stroke="' + EYE + '" stroke-width="0.9" stroke-linecap="round"/>' +
      '<path d="M56 76 Q58 78 60 76" stroke="' + EYE + '" stroke-width="0.9" fill="none" stroke-linecap="round"/>' +
      '<path d="M64 76 Q62 78 60 76" stroke="' + EYE + '" stroke-width="0.9" fill="none" stroke-linecap="round"/>' +
      // Whiskers
      '<path d="M40 72 L32 70 M40 74 L32 74 M80 72 L88 70 M80 74 L88 74" stroke="' + RABBIT_SH + '" stroke-width="0.7" stroke-linecap="round"/>'
    );
  }

  // =========================================================================
  // WATCHER — full body
  // =========================================================================
  function watcherSVG(state) {
    const vest = stateTint('vest', state);
    const vestH = stateTint('vestH', state);
    const star = stateTint('star', state);
    return (
      '<ellipse cx="60" cy="176" rx="32" ry="3.5" fill="' + GROUND_SHADOW + '" opacity="0.35"/>' +
      // Legs
      '<path d="M45 138 L42 168 L55 168 L58 138Z" fill="' + SUIT + '"/>' +
      '<path d="M62 138 L65 168 L78 168 L75 138Z" fill="' + SUIT + '"/>' +
      '<rect x="46" y="140" width="2.5" height="24" fill="' + SUIT_HI + '"/>' +
      '<rect x="63" y="140" width="2.5" height="24" fill="' + SUIT_HI + '"/>' +
      // Boots
      '<path d="M38 166 L60 166 Q62 172 58 176 L36 176 Q34 171 38 166Z" fill="' + BOOT + '"/>' +
      '<path d="M60 166 L82 166 Q86 171 84 176 L62 176 Q58 172 60 166Z" fill="' + BOOT + '"/>' +
      // Shirt body
      '<path d="M38 72 Q33 70 34 76 L34 136 L86 136 L86 76 Q87 70 82 72 L72 70 L48 70Z" fill="' + SHIRT + '"/>' +
      // Arms (shirt-sleeved)
      '<rect x="22" y="72" width="10" height="55" rx="4" fill="' + SHIRT + '"/>' +
      '<rect x="88" y="72" width="10" height="55" rx="4" fill="' + SHIRT + '"/>' +
      '<rect x="22" y="122" width="10" height="6" fill="' + vest + '"/>' +
      '<rect x="88" y="122" width="10" height="6" fill="' + vest + '"/>' +
      '<circle cx="27" cy="133" r="5" fill="' + SKIN + '"/>' +
      '<circle cx="93" cy="133" r="5" fill="' + SKIN + '"/>' +
      // Vest (two panels)
      '<path d="M30 72 Q26 75 28 82 L32 136 L50 136 L52 82 L50 72Z" fill="' + vest + '"/>' +
      '<path d="M90 72 Q94 75 92 82 L88 136 L70 136 L68 82 L70 72Z" fill="' + vest + '"/>' +
      '<path d="M32 78 L33 132 L38 132 L40 78Z" fill="' + vestH + '" opacity="0.7"/>' +
      // Belt
      '<rect x="30" y="131" width="60" height="6" fill="' + BOOT + '"/>' +
      '<rect x="56" y="130" width="8" height="8" fill="' + HAT_BAND + '"/>' +
      '<rect x="58" y="132" width="4" height="4" fill="' + MEDAL_SH + '"/>' +
      // Star badge (5-point)
      '<path d="M77 88 L79.5 94 L86 94.2 L80.8 98.5 L82.8 105 L77 101 L71.2 105 L73.2 98.5 L68 94.2 L74.5 94Z" fill="' + star + '"/>' +
      '<path d="M77 89.5 L78.8 94 L83 94.3 L80 97.8 L81.2 102 L77 99.3 L72.8 102 L74 97.8 L71 94.3 L75.2 94Z" fill="' + STAR_MID + '" opacity="0.5"/>' +
      // Bandana at neck
      '<path d="M46 64 L74 64 L72 73 L48 73Z" fill="' + BANDANA + '"/>' +
      '<path d="M46 64 L74 64 L73 66 L47 66Z" fill="' + BANDANA + '" opacity="0.6"/>' +
      // Neck
      '<rect x="55" y="58" width="10" height="8" fill="' + NECK + '"/>' +
      // Ears
      '<ellipse cx="40" cy="46" rx="2" ry="3.5" fill="' + NECK + '"/>' +
      '<ellipse cx="80" cy="46" rx="2" ry="3.5" fill="' + NECK + '"/>' +
      // Head
      '<ellipse cx="60" cy="45" rx="19" ry="20" fill="' + SKIN + '"/>' +
      '<path d="M43 43 Q46 32 55 30 Q50 42 46 54Z" fill="' + SKIN_HI + '" opacity="0.65"/>' +
      // Serious narrowed eyes
      '<path d="M48 45 Q51 43 55 45" stroke="' + EYE + '" stroke-width="1.6" fill="none" stroke-linecap="round"/>' +
      '<path d="M65 45 Q69 43 72 45" stroke="' + EYE + '" stroke-width="1.6" fill="none" stroke-linecap="round"/>' +
      // Nose
      '<path d="M58 50 Q60 55 62 50Z" fill="' + SKIN_SHADE + '"/>' +
      // Mouth (flat line)
      '<path d="M55 57 L65 57" stroke="' + BROW + '" stroke-width="1.2" stroke-linecap="round"/>' +
      // 5-o'clock shadow
      '<path d="M49 55 Q60 61 71 55 L71 59 Q60 63 49 59Z" fill="' + SUIT + '" opacity="0.22"/>' +
      // Hat brim (wide cowboy)
      '<ellipse cx="60" cy="28" rx="36" ry="5" fill="' + HAT + '"/>' +
      '<ellipse cx="60" cy="27" rx="36" ry="2" fill="' + HAT_HI + '"/>' +
      // Hat crown with pinch
      '<path d="M45 28 Q41 10 50 5 Q60 2 70 5 Q79 10 75 28Z" fill="' + HAT + '"/>' +
      '<path d="M60 5 L60 24" stroke="' + SUIT_SH + '" stroke-width="1.4"/>' +
      '<path d="M48 8 Q48 16 50 24" stroke="' + HAT_HI + '" stroke-width="1.3" fill="none"/>' +
      // Hat band
      '<rect x="45" y="23" width="30" height="3.5" fill="' + BRIM + '"/>'
    );
  }

  // =========================================================================
  // CLOCK — cuckoo clock (top half; pendulum is overlaid separately)
  // =========================================================================
  function clockSVG(state) {
    const frame = stateTint('frame', state);
    const frameH = stateTint('frameH', state);
    const face = state === 'aborted' ? '#9aa3b8' : CLOCK_FACE;
    return (
      // Roof shadow
      '<path d="M8 34 L40 6 L72 34 L68 36 L40 13 L12 36Z" fill="' + CLOCK_SH + '"/>' +
      // Roof main
      '<path d="M10 33 L40 8 L70 33 L66 34 L40 15 L14 34Z" fill="' + frame + '"/>' +
      // Roof highlight (sunlit side)
      '<path d="M14 33 L40 12 L40 16 L16 35Z" fill="' + frameH + '" opacity="0.75"/>' +
      // Roof peak ornament
      '<rect x="38" y="2" width="4" height="6" fill="' + CLOCK_BRASS + '"/>' +
      '<circle cx="40" cy="2" r="2" fill="' + CLOCK_BRASS + '"/>' +
      // Body (rounded rect)
      '<rect x="14" y="32" width="52" height="56" rx="5" fill="' + frame + '"/>' +
      '<rect x="60" y="36" width="5" height="48" fill="' + CLOCK_SH + '" opacity="0.55"/>' +
      '<rect x="16" y="34" width="4" height="52" fill="' + frameH + '" opacity="0.6"/>' +
      // Face circle
      '<circle cx="40" cy="56" r="17" fill="' + face + '"/>' +
      '<circle cx="40" cy="56" r="17" fill="none" stroke="' + frame + '" stroke-width="1.5"/>' +
      // Hour ticks (12, 3, 6, 9)
      '<rect x="39" y="41" width="2" height="3.5" fill="' + CLOCK_HAND + '"/>' +
      '<rect x="53" y="55" width="3.5" height="2" fill="' + CLOCK_HAND + '"/>' +
      '<rect x="39" y="68" width="2" height="3.5" fill="' + CLOCK_HAND + '"/>' +
      '<rect x="23.5" y="55" width="3.5" height="2" fill="' + CLOCK_HAND + '"/>' +
      // Minor ticks
      '<circle cx="46" cy="44" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="52" cy="50" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="52" cy="62" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="46" cy="68" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="34" cy="68" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="28" cy="62" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="28" cy="50" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      '<circle cx="34" cy="44" r="0.8" fill="' + CLOCK_HAND + '"/>' +
      // Hands (hour ~11, minute ~2)
      '<path d="M40 56 L38 46" stroke="' + CLOCK_HAND + '" stroke-width="1.8" stroke-linecap="round"/>' +
      '<path d="M40 56 L50 60" stroke="' + CLOCK_HAND + '" stroke-width="1.4" stroke-linecap="round"/>' +
      '<circle cx="40" cy="56" r="1.6" fill="' + CLOCK_HAND + '"/>' +
      // Cuckoo door (above pendulum hole)
      '<rect x="32" y="76" width="16" height="6" rx="1" fill="' + CLOCK_SH + '"/>' +
      '<path d="M40 76 L40 82" stroke="' + CLOCK_HAND + '" stroke-width="0.7"/>' +
      // Base
      '<rect x="12" y="88" width="56" height="7" fill="' + frame + '"/>' +
      '<rect x="10" y="95" width="60" height="5" fill="' + CLOCK_SH + '"/>' +
      // Weights hanging
      '<line x1="28" y1="100" x2="28" y2="115" stroke="' + CLOCK_SH + '" stroke-width="0.8"/>' +
      '<line x1="52" y1="100" x2="52" y2="115" stroke="' + CLOCK_SH + '" stroke-width="0.8"/>' +
      '<ellipse cx="28" cy="122" rx="4" ry="7" fill="' + CLOCK_BRASS + '"/>' +
      '<ellipse cx="52" cy="122" rx="4" ry="7" fill="' + CLOCK_BRASS + '"/>' +
      '<ellipse cx="27" cy="120" rx="1.2" ry="3" fill="' + CLOCK_HI + '" opacity="0.7"/>' +
      '<ellipse cx="51" cy="120" rx="1.2" ry="3" fill="' + CLOCK_HI + '" opacity="0.7"/>'
    );
  }

  // =========================================================================
  // PENDULUM — separate (animated via CSS)
  // =========================================================================
  function pendulumSVG(scale) {
    // scale unused in new design (we use a fixed viewBox); retained for API compat.
    void scale;
    return svgWrap(
      '0 0 20 50', '', 'clock-pendulum',
      '<line x1="10" y1="0" x2="10" y2="35" stroke="' + CLOCK_SH + '" stroke-width="1.2"/>' +
      '<circle cx="10" cy="40" r="7" fill="' + CLOCK_BRASS + '"/>' +
      '<circle cx="8" cy="38" r="2.2" fill="' + CLOCK_HI + '" opacity="0.85"/>'
    );
  }

  // =========================================================================
  // CUCKOO BIRD — pops out on transitions
  // =========================================================================
  function cuckooSVG(scale) {
    void scale;
    return svgWrap(
      '0 0 40 28', '', 'cuckoo-bird',
      '<ellipse cx="18" cy="16" rx="12" ry="9" fill="' + CLOCK_BRASS + '"/>' +
      '<ellipse cx="14" cy="12" rx="4" ry="3" fill="' + CLOCK_HI + '" opacity="0.8"/>' +
      '<circle cx="10" cy="13" r="5" fill="' + CLOCK_BRASS + '"/>' +
      '<path d="M4 13 L-1 15 L4 17Z" fill="' + BANDANA + '"/>' +
      '<circle cx="8" cy="12" r="1.1" fill="' + EYE + '"/>' +
      '<circle cx="7.7" cy="11.6" r="0.4" fill="' + EYE_WHITE + '"/>'
    );
  }

  // =========================================================================
  // SALOON BACKDROP — simplified nighttime interior silhouette
  // =========================================================================
  function saloonBackdropSVG() {
    const WALL = '#1a2236';
    const WALL_HI = '#263451';
    const SHELF = '#6b7890';
    const SHELF_DK = '#4a5568';
    const BOTTLE_A = '#2d6b6e';
    const BOTTLE_B = '#4a6d8f';
    const BOTTLE_C = '#9fb8d8';
    const COUNTER = '#2a2f3e';
    const FLOOR = '#353c4f';
    const BOARD = '#13192a';
    const LAMP = '#e8c170';

    const bottles = [];
    // Two horizontal shelves with repeating bottles
    for (let shelf = 0; shelf < 2; shelf++) {
      const y = 28 + shelf * 24;
      for (let i = 0; i < 18; i++) {
        const x = 18 + i * 18;
        // Skip mid-section where characters stand
        if (x > 130 && x < 200) continue;
        const color = [BOTTLE_A, BOTTLE_B, BOTTLE_C][(i + shelf) % 3];
        bottles.push('<rect x="' + x + '" y="' + y + '" width="8" height="14" rx="1.5" fill="' + color + '"/>');
        bottles.push('<rect x="' + (x + 2.5) + '" y="' + (y - 2) + '" width="3" height="3" fill="' + color + '"/>');
      }
    }
    return svgWrap(
      '0 0 330 120', 'saloon-backdrop', 'saloon-backdrop-svg',
      // Wall
      '<rect x="0" y="0" width="330" height="82" fill="' + WALL + '"/>' +
      // Wall highlight band
      '<rect x="0" y="12" width="330" height="4" fill="' + WALL_HI + '" opacity="0.5"/>' +
      // Lamp glows (two warm spots for mood)
      '<circle cx="60" cy="20" r="22" fill="' + LAMP + '" opacity="0.08"/>' +
      '<circle cx="270" cy="20" r="22" fill="' + LAMP + '" opacity="0.08"/>' +
      '<circle cx="60" cy="22" r="3" fill="' + LAMP + '" opacity="0.6"/>' +
      '<circle cx="270" cy="22" r="3" fill="' + LAMP + '" opacity="0.6"/>' +
      // Shelves
      '<rect x="10" y="44" width="310" height="3" fill="' + SHELF + '"/>' +
      '<rect x="10" y="44" width="310" height="1" fill="' + SHELF_DK + '" opacity="0.8"/>' +
      '<rect x="10" y="68" width="310" height="3" fill="' + SHELF + '"/>' +
      '<rect x="10" y="68" width="310" height="1" fill="' + SHELF_DK + '" opacity="0.8"/>' +
      bottles.join('') +
      // Trim board
      '<rect x="0" y="82" width="330" height="4" fill="' + BOARD + '"/>' +
      // Counter
      '<rect x="0" y="86" width="330" height="14" fill="' + COUNTER + '"/>' +
      '<rect x="0" y="86" width="330" height="2" fill="' + SHELF_DK + '" opacity="0.5"/>' +
      // Floor
      '<rect x="0" y="100" width="330" height="20" fill="' + FLOOR + '"/>' +
      '<rect x="0" y="100" width="330" height="1" fill="' + BOARD + '"/>'
    );
  }

  // =========================================================================
  // PORTRAITS — head + shoulders for dialogue panel
  // =========================================================================
  function mayorPortraitSVG() {
    return (
      '<ellipse cx="50" cy="95" rx="30" ry="18" fill="' + SUIT + '"/>' +
      '<path d="M28 75 Q26 70 30 68 L70 68 Q74 70 72 75 L72 110 L28 110Z" fill="' + SUIT + '"/>' +
      '<path d="M20 85 L80 95 L82 103 L22 93Z" fill="' + SASH + '"/>' +
      '<circle cx="48" cy="92" r="5" fill="' + MEDAL + '"/>' +
      '<circle cx="48" cy="92" r="2.5" fill="' + MEDAL_MID + '"/>' +
      '<rect x="44" y="62" width="12" height="8" fill="' + NECK + '"/>' +
      '<ellipse cx="50" cy="48" rx="22" ry="24" fill="' + SKIN + '"/>' +
      '<path d="M32 46 Q36 32 48 30 Q40 44 36 58Z" fill="' + SKIN_HI + '" opacity="0.6"/>' +
      '<circle cx="41" cy="48" r="2.2" fill="' + EYE + '"/>' +
      '<circle cx="59" cy="48" r="2.2" fill="' + EYE + '"/>' +
      '<circle cx="41.5" cy="47.3" r="0.7" fill="' + EYE_WHITE + '"/>' +
      '<circle cx="59.5" cy="47.3" r="0.7" fill="' + EYE_WHITE + '"/>' +
      '<path d="M37 42 L44 41 M56 41 L63 42" stroke="' + BROW + '" stroke-width="1.5" stroke-linecap="round"/>' +
      '<path d="M48 53 Q50 59 52 53Z" fill="' + SKIN_SHADE + '"/>' +
      '<path d="M38 62 Q46 68 50 65 Q54 68 62 62 Q56 70 50 68 Q44 70 38 62Z" fill="' + MEDAL + '"/>' +
      '<ellipse cx="50" cy="30" rx="32" ry="5" fill="' + HAT + '"/>' +
      '<path d="M30 30 L30 5 Q30 0 35 0 L65 0 Q70 0 70 5 L70 30Z" fill="' + HAT + '"/>' +
      '<rect x="30" y="22" width="40" height="6" fill="' + HAT_BAND + '"/>' +
      '<rect x="30" y="22" width="40" height="1.5" fill="' + HAT_BAND_HI + '"/>' +
      '<path d="M34 5 Q34 15 36 25" stroke="' + HAT_HI + '" stroke-width="2" fill="none" stroke-linecap="round"/>'
    );
  }

  function rabbitPortraitSVG() {
    return (
      '<path d="M30 85 Q28 95 30 105 L30 120 L70 120 L70 105 Q72 95 70 85Z" fill="' + APRON + '"/>' +
      '<rect x="28" y="80" width="44" height="6" fill="' + SASH + '"/>' +
      '<path d="M42 78 L38 84 L42 90 L50 87 L58 90 L62 84 L58 78 L50 82Z" fill="' + SASH + '"/>' +
      '<path d="M36 8 Q30 14 33 50 Q36 54 40 52 L44 10Z" fill="' + RABBIT_BODY + '"/>' +
      '<path d="M64 8 Q70 14 67 50 Q64 54 60 52 L56 10Z" fill="' + RABBIT_BODY + '"/>' +
      '<path d="M38 15 Q35 18 35 45 Q38 48 40 44Z" fill="' + RABBIT_INNER + '"/>' +
      '<path d="M62 15 Q65 18 65 45 Q62 48 60 44Z" fill="' + RABBIT_INNER + '"/>' +
      '<circle cx="50" cy="55" r="28" fill="' + RABBIT_BODY + '"/>' +
      '<path d="M28 50 Q32 34 44 30 Q36 50 32 66Z" fill="' + RABBIT_HI + '" opacity="0.6"/>' +
      '<ellipse cx="33" cy="64" rx="4" ry="2.5" fill="' + RABBIT_INNER + '" opacity="0.4"/>' +
      '<ellipse cx="67" cy="64" rx="4" ry="2.5" fill="' + RABBIT_INNER + '" opacity="0.4"/>' +
      '<circle cx="41" cy="52" r="2.8" fill="' + EYE + '"/>' +
      '<circle cx="59" cy="52" r="2.8" fill="' + EYE + '"/>' +
      '<circle cx="42" cy="51.2" r="0.9" fill="' + EYE_WHITE + '"/>' +
      '<circle cx="60" cy="51.2" r="0.9" fill="' + EYE_WHITE + '"/>' +
      '<path d="M47 61 Q50 66 53 61 Q50 64 47 61Z" fill="' + RABBIT_INNER + '"/>' +
      '<path d="M50 63 L50 67" stroke="' + EYE + '" stroke-width="0.9" stroke-linecap="round"/>' +
      '<path d="M46 69 Q48 71 50 69 M54 69 Q52 71 50 69" stroke="' + EYE + '" stroke-width="0.9" fill="none" stroke-linecap="round"/>'
    );
  }

  function watcherPortraitSVG() {
    return (
      '<ellipse cx="50" cy="95" rx="32" ry="18" fill="' + VEST + '"/>' +
      '<path d="M30 75 Q26 72 30 70 L70 70 Q74 72 70 75 L70 110 L30 110Z" fill="' + SHIRT + '"/>' +
      '<path d="M24 70 Q20 75 22 82 L26 110 L40 110 L42 80 L40 70Z" fill="' + VEST + '"/>' +
      '<path d="M76 70 Q80 75 78 82 L74 110 L60 110 L58 80 L60 70Z" fill="' + VEST + '"/>' +
      '<path d="M66 84 L68 90 L74 90 L69 94 L71 100 L66 96 L61 100 L63 94 L58 90 L64 90Z" fill="' + STAR + '"/>' +
      '<path d="M40 66 L60 66 L59 74 L41 74Z" fill="' + BANDANA + '"/>' +
      '<rect x="44" y="60" width="12" height="7" fill="' + NECK + '"/>' +
      '<ellipse cx="50" cy="48" rx="21" ry="22" fill="' + SKIN + '"/>' +
      '<path d="M32 46 Q36 34 48 32 Q40 46 36 58Z" fill="' + SKIN_HI + '" opacity="0.6"/>' +
      '<path d="M38 48 Q42 46 46 48" stroke="' + EYE + '" stroke-width="1.8" fill="none" stroke-linecap="round"/>' +
      '<path d="M54 48 Q58 46 62 48" stroke="' + EYE + '" stroke-width="1.8" fill="none" stroke-linecap="round"/>' +
      '<path d="M48 53 Q50 58 52 53Z" fill="' + SKIN_SHADE + '"/>' +
      '<path d="M44 60 L56 60" stroke="' + BROW + '" stroke-width="1.3" stroke-linecap="round"/>' +
      '<ellipse cx="50" cy="30" rx="38" ry="5" fill="' + HAT + '"/>' +
      '<path d="M32 30 Q28 12 38 6 Q50 3 62 6 Q72 12 68 30Z" fill="' + HAT + '"/>' +
      '<path d="M50 6 L50 28" stroke="' + SUIT_SH + '" stroke-width="1.2"/>' +
      '<rect x="32" y="24" width="36" height="4" fill="' + BRIM + '"/>'
    );
  }

  // =========================================================================
  // Banner-rendering wrappers
  // =========================================================================
  function renderMayor(state) {
    return svgWrap('0 0 120 180', 'mayor-sprite', 'character-sprite mayor-sprite-svg',
      mayorSVG(state));
  }
  function renderRabbit(state) {
    return svgWrap('0 0 120 180', 'rabbit-sprite', 'character-sprite rabbit-sprite-svg',
      rabbitSVG(state));
  }
  function renderWatcher(state) {
    return svgWrap('0 0 120 180', 'watcher-sprite', 'character-sprite watcher-sprite-svg',
      watcherSVG(state));
  }
  function renderClock(state) {
    return svgWrap('0 0 80 130', 'clock-sprite', 'character-sprite clock-sprite-svg',
      clockSVG(state));
  }
  function renderPendulum() {
    return pendulumSVG();
  }
  function renderCuckoo() {
    return cuckooSVG();
  }
  function renderSaloonBackdrop() {
    return saloonBackdropSVG();
  }

  function renderPortrait(speaker) {
    const s = (speaker || '').toLowerCase();
    let body;
    if (s === 'rabbit' || s === 'bartender') {
      body = rabbitPortraitSVG();
    } else if (s === 'watcher') {
      body = watcherPortraitSVG();
    } else {
      body = mayorPortraitSVG();
    }
    return svgWrap('0 0 100 120', '', 'portrait-svg', body);
  }

  function renderTitle() {
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
  };

})();
