/*!
 * Thesis Chart Renderer — Phase 5R
 * Wraps TradingView Lightweight Charts v4.2.0 into a clean white-background
 * candlestick chart with trade-level price lines and a title/timeframe overlay.
 * Exposed as window.ThesisChart (extended by chart_annotations.js).
 */
(function () {
  'use strict';

  var LWC = window.LightweightCharts;

  window.ThesisChart = {
    /**
     * Initialise a Lightweight Chart inside `container`.
     * @param {HTMLElement} container  Positioned parent div (position:relative, explicit height)
     * @param {Object}      payload    chart_payload.py output
     * @returns {Object} chart and series instances
     */
    init: function (container, payload) {
      var tp = payload.trade_plan || {};
      var pattern = payload.pattern || {};
      var compact = container.clientWidth < 520;

      var tradePrices = [tp.entry, tp.target, tp.stop]
        .map(function (value) { return Number(value); })
        .filter(function (value) { return Number.isFinite(value); });

      var chart = LWC.createChart(container, {
        width: container.clientWidth,
        height: container.clientHeight,
        layout: {
          background: { type: 'solid', color: '#ffffff' },
          textColor: '#343434',
          fontSize: 13,
        },
        grid: {
          vertLines: { color: '#eeeeee' },
          horzLines: { color: '#eeeeee' },
        },
        rightPriceScale: {
          visible: true,
          borderColor: '#e0e0e0',
          scaleMargins: { top: compact ? 0.18 : 0.22, bottom: compact ? 0.12 : 0.10 },
        },
        leftPriceScale: { visible: false },
        timeScale: {
          borderColor: '#e0e0e0',
          rightOffset: 18,
          barSpacing: Math.max(6, Math.min(10, container.clientWidth / 180)),
          timeVisible: false,
          secondsVisible: false,
          fixLeftEdge: true,
          fixRightEdge: false,
        },
        crosshair: {
          vertLine: { color: '#cccccc', labelBackgroundColor: '#888888' },
          horzLine: { color: '#cccccc', labelBackgroundColor: '#888888' },
        },
        handleScale: {
          mouseWheel: false,
          pinch: false,
          axisPressedMouseMove: false,
        },
        handleScroll: {
          mouseWheel: false,
          pressedMouseMove: false,
          horzTouchDrag: false,
          vertTouchDrag: false,
        },
      });

      var candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        priceLineVisible: !compact,
        lastValueVisible: !compact,
        autoscaleInfoProvider: function (baseImplementation) {
          var info = baseImplementation();
          if (!info || !info.priceRange || !tradePrices.length) return info;
          var minValue = info.priceRange.minValue;
          var maxValue = info.priceRange.maxValue;
          tradePrices.forEach(function (price) {
            minValue = Math.min(minValue, price);
            maxValue = Math.max(maxValue, price);
          });
          var span = Math.max(1, maxValue - minValue);
          return {
            priceRange: {
              minValue: minValue - span * 0.06,
              maxValue: maxValue + span * 0.08,
            },
            margins: info.margins,
          };
        },
      });

      candleSeries.setData(payload.candles);

      // Price lines for trade levels
      // LineStyle: 0=Solid, 1=Dotted, 2=Dashed, 3=LargeDashed, 4=SparseDotted
      if (tp.entry != null) {
        candleSeries.createPriceLine({
          price: tp.entry,
          color: '#ff6b00',
          lineWidth: 2,
          lineStyle: 2,
          axisLabelVisible: true,
          title: compact ? 'Entry' : 'Entry ' + _fmt(tp.entry),
        });
      }
      if (tp.target != null) {
        candleSeries.createPriceLine({
          price: tp.target,
          color: '#26a69a',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: compact ? 'Target' : 'Target ' + _fmt(tp.target),
        });
      }
      if (tp.stop != null) {
        candleSeries.createPriceLine({
          price: tp.stop,
          color: '#ef5350',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: compact ? 'Stop' : 'Stop ' + _fmt(tp.stop),
        });
      }

      chart.timeScale().fitContent();
      // Clip viewport so candles are never narrower than ~2 px on small screens.
      // All bars remain in memory so off-screen geometry indices still resolve.
      var numBars = (payload.candles || []).length;
      var maxReadableBars = Math.max(60, Math.floor(container.clientWidth / 2));
      if (numBars > maxReadableBars) {
        chart.timeScale().setVisibleLogicalRange({
          from: numBars - maxReadableBars,
          to: numBars - 1,
        });
      }
      chart.timeScale().applyOptions({ rightOffset: 18 });

      // Centered analyst-post style title overlay — no background, no border.
      var titleEl = document.createElement('div');
      titleEl.style.cssText = [
        'position:absolute',
        'top:14px',
        'left:0',
        'right:0',
        'padding:10px 16px',
        'text-align:center',
        'pointer-events:none',
        'z-index:4',
        'user-select:none',
      ].join(';');

      var statusColor = (function () {
        var s = (pattern.status || '').toUpperCase();
        if (s === 'BREAKING OUT') return '#16a34a';
        if (s === 'PIVOT READY')  return '#2563eb';
        return 'rgba(0,0,0,0.55)';
      }());

      var qualityScore = (pattern.quality_score != null && isFinite(pattern.quality_score))
        ? Number(pattern.quality_score)
        : null;
      var qualityColor = '#6b7280';
      var qualityLabel = '';
      if (qualityScore != null) {
        if (qualityScore >= 7.5)      { qualityColor = '#16a34a'; qualityLabel = 'TEXTBOOK'; }
        else if (qualityScore >= 5.0) { qualityColor = '#d97706'; qualityLabel = 'DECENT';   }
        else                          { qualityColor = '#dc2626'; qualityLabel = 'WEAK';     }
      }

      titleEl.innerHTML =
        '<div style="font-size:' + (compact ? '18px' : '30px') + ';font-weight:700;' +
        'color:rgba(0,0,0,0.85);font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'line-height:1.1;">' +
        _esc(payload.company_name || payload.symbol) +
        '</div>' +
        '<div style="font-size:' + (compact ? '11px' : '14px') + ';font-weight:600;' +
        'color:rgba(0,0,0,0.55);font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'margin-top:3px;">' +
        _esc((payload.timeframe || 'Daily') + ' Time Frame') +
        '</div>' +
        '<div style="font-size:' + (compact ? '10px' : '13px') + ';font-weight:700;' +
        'color:' + statusColor + ';font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'margin-top:2px;">' +
        _esc(pattern.type || 'Pattern') +
        (pattern.status ? ' | ' + _esc(pattern.status) : '') +
        '</div>' +
        (qualityScore != null
          ? '<div style="font-size:' + (compact ? '10px' : '12px') + ';font-weight:700;' +
            'color:' + qualityColor + ';font-family:Inter,Segoe UI,Arial,sans-serif;' +
            'margin-top:3px;letter-spacing:0.3px;">' +
            'PATTERN GRADE ' + qualityScore.toFixed(1) + '/10' +
            (qualityLabel ? ' • ' + qualityLabel : '') +
            '</div>'
          : '');
      container.appendChild(titleEl);

      // Ticker badge — top-right corner, text only.
      var tickerEl = document.createElement('div');
      tickerEl.style.cssText = [
        'position:absolute',
        'top:14px',
        'right:90px',
        'pointer-events:none',
        'z-index:4',
        'user-select:none',
        'font-size:14px',
        'font-weight:700',
        'color:#1d4ed8',
        'font-family:Inter,Segoe UI,Arial,sans-serif',
      ].join(';');
      tickerEl.textContent = payload.symbol || '';
      container.appendChild(tickerEl);

      // Bottom insight text — upside % and R:R centered above attribution
      var insightParts = [];
      if (tp.upside_pct != null) {
        insightParts.push('Upside +' + Number(tp.upside_pct).toFixed(1) + '%');
      }
      if (tp.reward_risk != null) {
        insightParts.push('R:R  ' + Number(tp.reward_risk).toFixed(1) + ':1');
      }
      if (insightParts.length) {
        var insightEl = document.createElement('div');
        insightEl.style.cssText = [
          'position:absolute',
          'bottom:28px',
          'left:0',
          'right:90px',
          'text-align:center',
          'pointer-events:none',
          'z-index:4',
          'user-select:none',
          'font-size:' + (compact ? '11px' : '14px'),
          'font-weight:700',
          'color:rgba(0,0,0,0.60)',
          'font-family:Inter,Segoe UI,Arial,sans-serif',
          'letter-spacing:0.03em',
        ].join(';');
        insightEl.textContent = insightParts.join('   ·   ');
        container.appendChild(insightEl);
      }

      // Resize
      if (typeof ResizeObserver !== 'undefined') {
        new ResizeObserver(function () {
          chart.applyOptions({
            width: container.clientWidth,
            height: container.clientHeight,
          });
        }).observe(container);
      }

      container.dataset.chartReady = '1';
      return { chart: chart, series: candleSeries };
    },
  };

  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _fmt(value) {
    var number = Number(value);
    if (!Number.isFinite(number)) return '';
    return number.toLocaleString('en-IN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  }
})();
