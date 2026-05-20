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
          scaleMargins: { top: compact ? 0.08 : 0.12, bottom: 0.08 },
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
      chart.timeScale().applyOptions({ rightOffset: 18 });

      // Compact thesis label. Keep it small so it does not hide candles.
      var titleEl = document.createElement('div');
      var titleRightPad = compact ? 86 : 96;
      titleEl.style.cssText = [
        'position:absolute',
        'top:12px',
        'left:' + (compact ? '10px' : '16px'),
        'right:' + titleRightPad + 'px',
        'max-width:' + (compact ? '70%' : '520px'),
        'padding:' + (compact ? '7px 8px' : '9px 11px'),
        'text-align:left',
        'pointer-events:none',
        'z-index:4',
        'user-select:none',
        'background:rgba(255,255,255,0.82)',
        'border:1px solid rgba(20,20,20,0.08)',
        'border-radius:6px',
        'box-shadow:0 8px 18px rgba(255,255,255,0.48)',
      ].join(';');
      titleEl.innerHTML =
        '<div style="font-size:' + (compact ? '16px' : '24px') + ';font-weight:900;' +
        'color:rgba(0,0,0,0.72);font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'line-height:1.05;letter-spacing:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
        _esc(payload.company_name || payload.symbol) +
        '</div>' +
        '<div style="font-size:' + (compact ? '11px' : '13px') + ';font-weight:800;' +
        'color:rgba(0,0,0,0.58);font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'margin-top:4px;letter-spacing:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
        _esc((payload.timeframe || 'Daily') + ' Time Frame') +
        ' | ' +
        _esc(pattern.type || 'Pattern') +
        (pattern.status ? ' | ' + _esc(pattern.status) : '') +
        '</div>';
      container.appendChild(titleEl);

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
