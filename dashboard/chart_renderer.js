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
          scaleMargins: { top: 0.05, bottom: 0.08 },
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
          title: 'Entry ' + _fmt(tp.entry),
        });
      }
      if (tp.target != null) {
        candleSeries.createPriceLine({
          price: tp.target,
          color: '#26a69a',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: 'Target ' + _fmt(tp.target),
        });
      }
      if (tp.stop != null) {
        candleSeries.createPriceLine({
          price: tp.stop,
          color: '#ef5350',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: 'Stop ' + _fmt(tp.stop),
        });
      }

      chart.timeScale().fitContent();
      chart.timeScale().applyOptions({ rightOffset: 18 });

      // Title / watermark overlay (HTML layer, pointer-events off)
      var titleEl = document.createElement('div');
      titleEl.style.cssText = [
        'position:absolute',
        'top:8%',
        'left:0',
        'right:68px',
        'text-align:center',
        'pointer-events:none',
        'z-index:2',
        'user-select:none',
      ].join(';');
      titleEl.innerHTML =
        '<div style="font-size:clamp(28px,4vw,62px);font-weight:900;' +
        'color:rgba(0,0,0,0.72);font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'text-shadow:0 0 10px rgba(255,255,255,0.98),0 0 3px #fff;' +
        'line-height:1.08;letter-spacing:0;">' +
        _esc(payload.company_name || payload.symbol) +
        '</div>' +
        '<div style="font-size:clamp(16px,2vw,30px);font-weight:800;' +
        'color:rgba(0,0,0,0.58);font-family:Inter,Segoe UI,Arial,sans-serif;' +
        'text-shadow:0 0 8px rgba(255,255,255,0.95),0 0 3px #fff;' +
        'margin-top:10px;letter-spacing:0;">' +
        _esc((payload.timeframe || 'Daily') + ' Time Frame') +
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
