/*!
 * Thesis Chart Annotations — Phase 5R
 * Extends window.ThesisChart.init (from chart_renderer.js) with a canvas
 * overlay that draws trade-level boxes (upside/downside zones) anchored to
 * the chart's price coordinate system.
 *
 * Depends on: chart_renderer.js loaded first (ThesisChart global present).
 */
(function () {
  'use strict';

  // Wrap the renderer's init to attach annotation canvas after base setup
  var _baseInit = window.ThesisChart.init.bind(window.ThesisChart);

  window.ThesisChart.init = function (container, payload) {
    var inst = _baseInit(container, payload);
    if (!inst) return inst;

    var tp = payload.trade_plan || {};
    // Annotations only make sense when all three levels are present
    if (tp.entry == null || tp.target == null || tp.stop == null) return inst;

    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:3;';
    container.appendChild(canvas);

    function redraw() {
      var W = container.clientWidth;
      var H = container.clientHeight;
      if (!W || !H) return;

      canvas.width = W;
      canvas.height = H;

      var ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, W, H);

      var entryY = inst.series.priceToCoordinate(tp.entry);
      var targetY = inst.series.priceToCoordinate(tp.target);
      var stopY = inst.series.priceToCoordinate(tp.stop);
      if (entryY == null || targetY == null || stopY == null) return;

      // Box region: future space just after the latest candle, before price scale.
      var compact = W < 520;
      var psW = compact ? Math.max(54, Math.min(70, W * 0.18)) : Math.min(86, W * 0.13);
      var boxEnd = W - psW - (compact ? 8 : 10);
      var lastCandle = (payload.candles || [])[payload.candles.length - 1];
      var lastX = lastCandle ? inst.chart.timeScale().timeToCoordinate(lastCandle.time) : null;
      var preferredStart = Number.isFinite(lastX) ? lastX + (compact ? 10 : 18) : W * (compact ? 0.55 : 0.68);
      var minBoxW = compact ? Math.max(84, Math.min(112, W * 0.26)) : 150;
      var minStart = compact ? W * 0.42 : W * 0.58;
      var maxStart = Math.max(8, boxEnd - minBoxW);
      var boxStart = minStart > maxStart ? maxStart : clamp(preferredStart, minStart, maxStart);
      var boxW = Math.max(48, boxEnd - boxStart);

      drawPatternOverlay(ctx, inst, payload, W, H, boxStart);

      // --- Upside zone (entry → target) ---
      var uTop = Math.min(entryY, targetY);
      var uBot = Math.max(entryY, targetY);
      var uH = uBot - uTop;

      if (uH > 3) {
        ctx.fillStyle = 'rgba(38,166,154,0.15)';
        ctx.fillRect(boxStart, uTop, boxW, uH);
        ctx.strokeStyle = 'rgba(38,166,154,0.62)';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(boxStart + 0.5, uTop + 0.5, boxW - 1, uH - 1);

        if (tp.upside_pct != null && uH > 18) {
          ctx.fillStyle = '#26a69a';
          ctx.font = 'bold ' + (compact ? 15 : 18) + 'px Inter,Arial,sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText('Upside +' + tp.upside_pct.toFixed(1) + '%', boxStart + boxW / 2, uTop + uH / 2, boxW - 10);
        }
      }

      // --- Downside zone (entry → stop) ---
      var dTop = Math.min(entryY, stopY);
      var dBot = Math.max(entryY, stopY);
      var dH = dBot - dTop;

      if (dH > 3) {
        ctx.fillStyle = 'rgba(239,83,80,0.12)';
        ctx.fillRect(boxStart, dTop, boxW, dH);
        ctx.strokeStyle = 'rgba(239,83,80,0.55)';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(boxStart + 0.5, dTop + 0.5, boxW - 1, dH - 1);

        if (tp.downside_pct != null && dH > 18) {
          ctx.fillStyle = '#ef5350';
          ctx.font = 'bold ' + (compact ? 14 : 16) + 'px Inter,Arial,sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText('Risk -' + tp.downside_pct.toFixed(1) + '%', boxStart + boxW / 2, dTop + dH / 2, boxW - 10);
        }
      }

      // --- R:R label below both boxes ---
      if (tp.reward_risk != null && W >= 520) {
        var rrY = Math.max(uBot, dBot) + 14;
        if (rrY < H - 28) {
          ctx.fillStyle = 'rgba(30,30,30,0.72)';
          ctx.font = 'bold 14px Inter,Arial,sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.fillText('R:R ' + tp.reward_risk.toFixed(1) + ':1', boxStart + boxW / 2, rrY);
        }
      }
    }

    // Trigger redraws when the chart scrolls or resizes
    inst.chart.timeScale().subscribeVisibleTimeRangeChange(redraw);
    inst.chart.subscribeCrosshairMove(redraw);

    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () {
        setTimeout(redraw, 60);
      }).observe(container);
    }

    // Initial draw after layout settles
    setTimeout(redraw, 120);

    return inst;
  };

  function drawPatternOverlay(ctx, inst, payload, W, H, futureStartX) {
    var pattern = payload.pattern || {};
    var geometry = pattern.geometry || {};
    var type = String(pattern.type || '').toLowerCase();
    if (!type || !Object.keys(geometry).length) return;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    if (type.indexOf('cup') >= 0) {
      drawCupHandle(ctx, inst, payload, geometry);
    } else if (type.indexOf('ascending triangle') >= 0) {
      drawAscendingTriangle(ctx, inst, payload, geometry);
    } else if (type.indexOf('bull flag') >= 0) {
      drawBullFlag(ctx, inst, payload, geometry);
    } else if (type === 'vcp' || type.indexOf('volatility contraction') >= 0) {
      drawVcp(ctx, inst, payload, geometry);
    } else if (type.indexOf('head') >= 0 && type.indexOf('shoulder') >= 0) {
      drawInverseHeadShoulders(ctx, inst, payload, geometry);
    } else if (type.indexOf('supertrend') >= 0) {
      drawSupertrend(ctx, inst, payload, geometry, futureStartX, H);
    } else if (type.indexOf('multi-year') >= 0 || type.indexOf('multiyear') >= 0) {
      drawMultiYearBreakout(ctx, inst, payload, geometry);
    }

    ctx.restore();
  }

  function drawCupHandle(ctx, inst, payload, geometry) {
    var left = pointFromRelativeIndex(inst, payload, geometry.left_rim_idx, 'high');
    var trough = pointFromRelativeIndex(inst, payload, geometry.trough_idx, 'low');
    var right = pointFromRelativeIndex(inst, payload, geometry.right_rim_idx, 'high');
    if (!left || !trough || !right) return;

    ctx.strokeStyle = 'rgba(0,0,0,0.78)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(left.x, left.y);
    ctx.quadraticCurveTo(trough.x, trough.y + 24, right.x, right.y);
    ctx.stroke();
    drawSimplePatternLabel(ctx, trough.x, trough.y + 30, 'Cup Base', '#111111');
    drawDot(ctx, left.x, left.y, '#111111');
    drawDot(ctx, trough.x, trough.y, '#111111');
    drawDot(ctx, right.x, right.y, '#111111');

    var handle = pointFromRelativeIndex(inst, payload, geometry.handle_start_idx, 'high');
    if (handle) {
      ctx.strokeStyle = 'rgba(0,0,0,0.68)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(handle.x, handle.y);
      ctx.lineTo(right.x, right.y);
      ctx.stroke();
      drawSimplePatternLabel(ctx, handle.x, handle.y - 18, 'Handle', '#111111');
    }

    var entry = Number((payload.trade_plan || {}).entry);
    var pivotY = Number.isFinite(entry) ? inst.series.priceToCoordinate(entry) : null;
    if (pivotY != null) {
      drawSegment(ctx, left.x, pivotY, latestCandleX(inst, payload), pivotY, '#2563eb', [5, 5], 2);
      drawSimplePatternLabel(ctx, right.x + 10, pivotY - 18, 'Breakout Rim', '#1d4ed8');
    }
  }

  function drawAscendingTriangle(ctx, inst, payload, geometry) {
    var lows = (geometry.low_indices || [])
      .map(function (idx) { return pointFromRelativeIndex(inst, payload, idx, 'low'); })
      .filter(Boolean);
    var touches = (geometry.touch_indices || [])
      .map(function (idx) { return pointFromRelativeIndex(inst, payload, idx, 'high'); })
      .filter(Boolean);
    var entry = Number((payload.trade_plan || {}).entry);
    var resistanceY = Number.isFinite(entry) ? inst.series.priceToCoordinate(entry) : null;
    if (resistanceY == null || (!lows.length && !touches.length)) return;

    var startX = touches.length ? touches[0].x : lows[0].x;
    var endX = touches.length ? touches[touches.length - 1].x : latestCandleX(inst, payload);
    endX = Math.max(endX, latestCandleX(inst, payload));
    ctx.strokeStyle = '#1f2937';
    ctx.setLineDash([6, 5]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(startX, resistanceY);
    ctx.lineTo(endX, resistanceY);
    ctx.stroke();
    ctx.setLineDash([]);
    drawSimplePatternLabel(ctx, Math.min(endX - 130, startX + 8), resistanceY - 20, 'Flat Resistance', '#1f2937');
    touches.forEach(function (point) { drawDot(ctx, point.x, resistanceY, '#1f2937'); });

    if (lows.length >= 2) {
      var first = lows[0];
      var last = lows[lows.length - 1];
      ctx.strokeStyle = '#111111';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(first.x, first.y);
      ctx.lineTo(last.x, last.y);
      ctx.stroke();

      ctx.fillStyle = 'rgba(59,130,246,0.10)';
      ctx.beginPath();
      ctx.moveTo(startX, resistanceY);
      ctx.lineTo(endX, resistanceY);
      ctx.lineTo(last.x, last.y);
      ctx.lineTo(first.x, first.y);
      ctx.closePath();
      ctx.fill();
      drawSimplePatternLabel(ctx, last.x + 16, last.y - 14, 'Rising Support', '#111111');
      lows.forEach(function (point) { drawDot(ctx, point.x, point.y, '#111111'); });
    }
  }

  function drawBullFlag(ctx, inst, payload, geometry) {
    var poleStart = pointFromAbsoluteIndex(inst, payload, geometry.pole_start_idx, 'close');
    var poleEnd = pointFromAbsoluteIndex(inst, payload, geometry.pole_end_idx, 'close');
    if (poleStart && poleEnd) {
      ctx.strokeStyle = '#16a34a';
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(poleStart.x, poleStart.y);
      ctx.lineTo(poleEnd.x, poleEnd.y);
      ctx.stroke();
      drawSimplePatternLabel(ctx, poleEnd.x + 8, poleEnd.y - 20, 'Pole', '#16a34a');
    }
    var candles = payload.candles || [];
    var flagLen = Math.max(5, Number(geometry.flag_len || 16));
    var start = Math.max(0, candles.length - flagLen);
    var flag = rangePoints(inst, candles, start, candles.length - 1);
    if (flag) {
      ctx.fillStyle = 'rgba(37,99,235,0.10)';
      ctx.fillRect(flag.x1, flag.highY, flag.x2 - flag.x1, flag.lowY - flag.highY);
      ctx.strokeStyle = 'rgba(37,99,235,0.70)';
      ctx.lineWidth = 2;
      ctx.strokeRect(flag.x1, flag.highY, flag.x2 - flag.x1, flag.lowY - flag.highY);
      drawSimplePatternLabel(ctx, flag.x1 + 8, flag.highY - 18, 'Flag Pullback', '#1d4ed8');
    }
  }

  function drawVcp(ctx, inst, payload, geometry) {
    var candles = payload.candles || [];
    var start = Math.max(0, candles.length - Number((payload.pattern || {}).bars_in_pattern || 60));
    var end = candles.length - 1;
    var startX = candleX(inst, candles[start]);
    var endX = candleX(inst, candles[end]);
    var entry = Number((payload.trade_plan || {}).entry);
    var y = Number.isFinite(entry) ? inst.series.priceToCoordinate(entry) : null;
    if (startX == null || endX == null || y == null) return;

    var values = geometry.contractions_pct || geometry.contractions || [];
    var count = 3;
    for (var i = 0; i < count; i += 1) {
      var partStart = Math.round(start + ((end - start + 1) / count) * i);
      var partEnd = Math.min(end, Math.round(start + ((end - start + 1) / count) * (i + 1)) - 1);
      var box = rangePoints(inst, candles, partStart, partEnd);
      if (!box) continue;
      var alpha = 0.08 + i * 0.04;
      ctx.fillStyle = 'rgba(37,99,235,' + alpha.toFixed(2) + ')';
      ctx.fillRect(box.x1, box.highY, box.x2 - box.x1, box.lowY - box.highY);
      ctx.strokeStyle = 'rgba(37,99,235,0.72)';
      ctx.lineWidth = 2;
      ctx.strokeRect(box.x1, box.highY, box.x2 - box.x1, box.lowY - box.highY);
      var suffix = values[i] != null ? ' ' + Number(values[i]).toFixed(1) + '%' : '';
      drawSimplePatternLabel(ctx, box.x1 + 6, box.highY + 14, 'C' + (i + 1) + suffix, '#1d4ed8');
    }

    drawSegment(ctx, Math.max(startX, endX - 180), y, endX, y, '#1d4ed8', [6, 5], 2);
    drawSimplePatternLabel(ctx, Math.max(startX + 8, endX - 150), y - 24, 'VCP Pivot', '#1d4ed8');
  }

  function drawInverseHeadShoulders(ctx, inst, payload, geometry) {
    var ls = pointFromRelativeIndex(inst, payload, geometry.left_shoulder_idx, 'low');
    var head = pointFromRelativeIndex(inst, payload, geometry.head_idx, 'low');
    var rs = pointFromRelativeIndex(inst, payload, geometry.right_shoulder_idx, 'low');
    if (!ls || !head || !rs) return;
    ctx.strokeStyle = '#111111';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(ls.x, ls.y);
    ctx.quadraticCurveTo((ls.x + head.x) / 2, head.y + 18, head.x, head.y);
    ctx.quadraticCurveTo((head.x + rs.x) / 2, head.y + 18, rs.x, rs.y);
    ctx.stroke();
    drawDot(ctx, ls.x, ls.y, '#111111');
    drawDot(ctx, head.x, head.y, '#111111');
    drawDot(ctx, rs.x, rs.y, '#111111');
    drawSimplePatternLabel(ctx, head.x - 28, head.y + 26, 'Head', '#111111');

    var neckline = Number(geometry.neckline || (payload.trade_plan || {}).entry);
    var necklineY = Number.isFinite(neckline) ? inst.series.priceToCoordinate(neckline) : null;
    if (necklineY != null) {
      drawSegment(ctx, ls.x, necklineY, latestCandleX(inst, payload), necklineY, '#2563eb', [6, 5], 2);
      drawSimplePatternLabel(ctx, rs.x + 10, necklineY - 18, 'Neckline', '#1d4ed8');
    }
  }

  function drawSupertrend(ctx, inst, payload, geometry, futureStartX, H) {
    var line = Number(geometry.supertrend || (payload.trade_plan || {}).stop);
    var y = Number.isFinite(line) ? inst.series.priceToCoordinate(line) : null;
    if (y == null) {
      drawSimplePatternLabel(ctx, futureStartX, H * 0.82, 'Bullish Flip', '#2563eb');
      return;
    }
    var startX = Math.max(8, latestCandleX(inst, payload) - 220);
    drawSegment(ctx, startX, y, latestCandleX(inst, payload), y, '#2563eb', [8, 5], 2);
    drawSimplePatternLabel(ctx, startX + 8, y - 20, 'Supertrend Support', '#1d4ed8');
  }

  function drawMultiYearBreakout(ctx, inst, payload, geometry) {
    var entry = Number((payload.trade_plan || {}).entry);
    var y = Number.isFinite(entry) ? inst.series.priceToCoordinate(entry) : null;
    if (y == null) return;
    var firstTouch = (geometry.resistance_touch_indices || [0])[0];
    var start = pointFromRelativeIndex(inst, payload, firstTouch, 'high');
    var endX = latestCandleX(inst, payload);
    if (!start || endX == null) return;
    ctx.strokeStyle = '#111111';
    ctx.setLineDash([3, 5]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(start.x, y);
    ctx.lineTo(endX, y);
    ctx.stroke();
    ctx.setLineDash([]);
    drawSimplePatternLabel(ctx, endX - 120, y - 24, 'Long Resistance', '#111111');
  }

  function pointFromRelativeIndex(inst, payload, idx, field) {
    var chartIndex = relativeChartIndex(payload, idx);
    return pointFromChartIndex(inst, payload, chartIndex, field);
  }

  function pointFromAbsoluteIndex(inst, payload, idx, field) {
    var chartIndex = absoluteChartIndex(payload, idx);
    return pointFromChartIndex(inst, payload, chartIndex, field);
  }

  function pointFromChartIndex(inst, payload, chartIndex, field) {
    var candles = payload.candles || [];
    if (chartIndex == null || chartIndex < 0 || chartIndex >= candles.length) return null;
    var candle = candles[chartIndex];
    var x = candleX(inst, candle);
    var y = inst.series.priceToCoordinate(Number(candle[field]));
    if (x == null || y == null) return null;
    return { x: x, y: y };
  }

  function relativeChartIndex(payload, idx) {
    var number = toNumber(idx);
    if (number == null) return null;
    var candles = payload.candles || [];
    var sourceRows = Number(payload.source_rows || candles.length);
    var visibleStart = Number(payload.visible_start_index || Math.max(0, sourceRows - candles.length));
    var patternBars = Number((payload.pattern || {}).bars_in_pattern || candles.length);
    var raw = sourceRows - patternBars + number;
    var mapped = raw - visibleStart;
    if (mapped >= 0 && mapped < candles.length) return Math.round(mapped);
    if (number >= 0 && number < candles.length) return Math.round(number);
    return null;
  }

  function absoluteChartIndex(payload, idx) {
    var number = toNumber(idx);
    if (number == null) return null;
    var candles = payload.candles || [];
    var sourceRows = Number(payload.source_rows || candles.length);
    var visibleStart = Number(payload.visible_start_index || Math.max(0, sourceRows - candles.length));
    var mapped = number - visibleStart;
    if (mapped >= 0 && mapped < candles.length) return Math.round(mapped);
    if (number >= 0 && number < candles.length) return Math.round(number);
    return null;
  }

  function candleX(inst, candle) {
    if (!candle) return null;
    return inst.chart.timeScale().timeToCoordinate(candle.time);
  }

  function latestCandleX(inst, payload) {
    var candles = payload.candles || [];
    return candleX(inst, candles[candles.length - 1]) || 0;
  }

  function rangePoints(inst, candles, start, end) {
    if (!candles.length || start > end) return null;
    start = clamp(Math.round(start), 0, candles.length - 1);
    end = clamp(Math.round(end), 0, candles.length - 1);
    var high = -Infinity;
    var low = Infinity;
    for (var i = start; i <= end; i += 1) {
      high = Math.max(high, Number(candles[i].high));
      low = Math.min(low, Number(candles[i].low));
    }
    var x1 = candleX(inst, candles[start]);
    var x2 = candleX(inst, candles[end]);
    var highY = inst.series.priceToCoordinate(high);
    var lowY = inst.series.priceToCoordinate(low);
    if (x1 == null || x2 == null || highY == null || lowY == null) return null;
    return {
      x1: Math.min(x1, x2),
      x2: Math.max(x1, x2),
      highY: Math.min(highY, lowY),
      lowY: Math.max(highY, lowY),
    };
  }

  function drawSegment(ctx, x1, y1, x2, y2, color, dash, width) {
    if (![x1, y1, x2, y2].every(Number.isFinite)) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 2;
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.restore();
  }

  function drawDot(ctx, x, y, color) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    ctx.save();
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  function toNumber(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function HSafe(inst) {
    return inst.chart.paneSize ? inst.chart.paneSize().height : 600;
  }

  function drawSimplePatternLabel(ctx, x, y, text, color) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    var compact = ctx.canvas.width < 520;
    var fontSize = compact ? 10 : 13;
    ctx.font = 'bold ' + fontSize + 'px Inter,Arial,sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    var width = Math.ceil(ctx.measureText(text).width) + (compact ? 10 : 14);
    var height = fontSize + (compact ? 9 : 12);
    var priceScaleWidth = Math.max(64, Math.min(86, ctx.canvas.width * 0.18));
    var plotRight = ctx.canvas.width - priceScaleWidth - 8;
    var boxX = clamp(x - 6, 8, Math.max(8, plotRight - width));
    var boxY = clamp(y - height / 2, 8, Math.max(8, ctx.canvas.height - height - 8));
    ctx.fillStyle = 'rgba(255,255,255,0.90)';
    ctx.fillRect(boxX, boxY, width, height);
    ctx.strokeStyle = 'rgba(0,0,0,0.12)';
    ctx.strokeRect(boxX, boxY, width, height);
    ctx.fillStyle = color;
    ctx.fillText(text, boxX + (compact ? 5 : 6), boxY + height / 2, width - 8);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(value, max));
  }
})();
