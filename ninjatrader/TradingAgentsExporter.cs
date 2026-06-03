#region Using declarations
using System;
using System.IO;
using System.Text;
using System.Globalization;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

// Indicador puente para la app TradingAgents.
// Exporta contexto multi-TF (velas por TF) + ORDER FLOW (delta/CVD/volume profile, Level 1)
// a un JSON que lee la app. La carpeta default apunta a la PC de casa; ajustala en las
// propiedades del indicador segun la PC (en la otra PC es C:\Users\Soles\Documents\TradingAgents\data).
namespace NinjaTrader.NinjaScript.Indicators
{
    public class TradingAgentsExporter : Indicator
    {
        [NinjaScriptProperty]
        [Display(Name = "Carpeta de exportación", Order = 1, GroupName = "Parameters")]
        public string ExportFolder { get; set; }

        // --- Order flow (Fase 2a): clasificacion bid/ask via OnMarketData ---
        private double curBid, curAsk;
        private long sessionCvd;
        private long bar1mDelta, bar1mMax, bar1mMin, bar1mVol;
        private long bar5mDelta, bar5mVol;
        private readonly List<long[]> of1m = new List<long[]>();   // {d, cvd, v, mx, mn}
        private readonly List<long[]> of5m = new List<long[]>();   // {d, cvd, v}
        private readonly Dictionary<double, long> volByPrice = new Dictionary<double, long>();

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "TradingAgentsExporter";
                Description = "Exporta contexto multi-TF + order flow a un JSON para la app TradingAgents.";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = true;
                ExportFolder = @"e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents\data";
            }
            else if (State == State.Configure)
            {
                AddDataSeries(BarsPeriodType.Minute, 5);   // index 1
                AddDataSeries(BarsPeriodType.Minute, 15);  // index 2
                AddDataSeries(BarsPeriodType.Minute, 60);  // index 3
                AddDataSeries(BarsPeriodType.Day, 1);      // index 4
            }
        }

        // Clasifica cada operacion: en el ASK = compra agresiva, en el BID = venta agresiva.
        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (BarsInProgress != 0) return;
            if (State != State.Realtime) return;

            if (e.MarketDataType == MarketDataType.Ask) { curAsk = e.Price; return; }
            if (e.MarketDataType == MarketDataType.Bid) { curBid = e.Price; return; }
            if (e.MarketDataType != MarketDataType.Last) return;

            long vol = (long)e.Volume;
            double price = e.Price;
            long signed = 0;
            if (curAsk > 0 && price >= curAsk) signed = vol;
            else if (curBid > 0 && price <= curBid) signed = -vol;

            sessionCvd += signed;
            bar1mDelta += signed; bar1mVol += vol;
            bar5mDelta += signed; bar5mVol += vol;
            if (bar1mDelta > bar1mMax) bar1mMax = bar1mDelta;
            if (bar1mDelta < bar1mMin) bar1mMin = bar1mDelta;

            double key = Instrument.MasterInstrument.RoundToTickSize(price);
            long cur;
            volByPrice.TryGetValue(key, out cur);
            volByPrice[key] = cur + vol;
        }

        protected override void OnBarUpdate()
        {
            if (State != State.Realtime) return;

            if (BarsInProgress == 1)
            {
                of5m.Add(new long[] { bar5mDelta, sessionCvd, bar5mVol });
                if (of5m.Count > 15) of5m.RemoveAt(0);
                bar5mDelta = 0; bar5mVol = 0;
                return;
            }
            if (BarsInProgress != 0) return;

            if (Bars.IsFirstBarOfSession)
            {
                sessionCvd = 0; of1m.Clear(); of5m.Clear(); volByPrice.Clear();
            }
            of1m.Add(new long[] { bar1mDelta, sessionCvd, bar1mVol, bar1mMax, bar1mMin });
            if (of1m.Count > 40) of1m.RemoveAt(0);
            bar1mDelta = 0; bar1mVol = 0; bar1mMax = 0; bar1mMin = 0;

            if (CurrentBars[0] < 1) return;
            if (CurrentBars[1] < 0 || CurrentBars[2] < 0 || CurrentBars[3] < 0) return;
            if (CurrentBars[4] < 1) return;

            try
            {
                string json = BuildJson();
                Directory.CreateDirectory(ExportFolder);
                File.WriteAllText(Path.Combine(ExportFolder, "live_" + Instrument.MasterInstrument.Name + ".json"), json);
            }
            catch (Exception ex)
            {
                Print("TradingAgentsExporter error: " + ex.Message);
            }
        }

        private static string F(double v) { return v.ToString(CultureInfo.InvariantCulture); }

        private string BuildJson()
        {
            var sb = new StringBuilder();
            sb.Append("{");
            sb.AppendFormat("\"par\":\"{0}\",", Instrument.MasterInstrument.Name);
            sb.AppendFormat("\"timestamp\":\"{0}\",", Times[0][0].ToString("yyyy-MM-ddTHH:mm:ss"));
            sb.AppendFormat("\"precio_actual\":{0},", F(Closes[0][0]));

            sb.Append("\"timeframes\":{");
            sb.Append("\"1m\":{\"ultimas_barras\":");  AppendBars(sb, 0, 120); sb.Append("},");
            sb.Append("\"5m\":{\"ultimas_barras\":");  AppendBars(sb, 1, 80);  sb.Append("},");
            sb.Append("\"15m\":{\"ultimas_barras\":"); AppendBars(sb, 2, 60);  sb.Append("},");
            sb.Append("\"1h\":{\"ultimas_barras\":");  AppendBars(sb, 3, 48);  sb.Append("},");
            sb.Append("\"1d\":{\"ultimas_barras\":");  AppendBars(sb, 4, 10);  sb.Append("}");
            sb.Append("},");

            sb.Append("\"niveles_clave\":{");
            sb.AppendFormat("\"pdh\":{0},\"pdl\":{1},\"dia_high\":{2},\"dia_low\":{3}",
                F(Highs[4][1]), F(Lows[4][1]), F(Highs[4][0]), F(Lows[4][0]));
            sb.Append("},");

            sb.Append("\"order_flow\":{");
            sb.AppendFormat("\"nota\":\"delta desde trades ejecutados (Level 1), no DOM\",\"cvd_sesion\":{0},", sessionCvd);
            sb.Append("\"1m\":"); AppendOf(sb, of1m, true); sb.Append(",");
            sb.Append("\"5m\":"); AppendOf(sb, of5m, false);
            sb.Append("},");

            sb.Append("\"volume_profile\":{\"por_nivel\":{");
            AppendProfile(sb, 60);
            sb.Append("}}");

            sb.Append("}");
            return sb.ToString();
        }

        private void AppendBars(StringBuilder sb, int seriesIdx, int maxBars)
        {
            sb.Append("[");
            int n = Math.Min(maxBars, CurrentBars[seriesIdx] + 1);
            for (int i = n - 1; i >= 0; i--)
            {
                sb.AppendFormat("{{\"o\":{0},\"h\":{1},\"l\":{2},\"c\":{3},\"v\":{4}}}",
                    F(Opens[seriesIdx][i]), F(Highs[seriesIdx][i]), F(Lows[seriesIdx][i]), F(Closes[seriesIdx][i]), F(Volumes[seriesIdx][i]));
                if (i > 0) sb.Append(",");
            }
            sb.Append("]");
        }

        private void AppendOf(StringBuilder sb, List<long[]> list, bool conMaxMin)
        {
            sb.Append("[");
            for (int i = 0; i < list.Count; i++)
            {
                long[] r = list[i];
                if (conMaxMin)
                    sb.AppendFormat("{{\"d\":{0},\"cd\":{1},\"v\":{2},\"mx\":{3},\"mn\":{4}}}", r[0], r[1], r[2], r[3], r[4]);
                else
                    sb.AppendFormat("{{\"d\":{0},\"cd\":{1},\"v\":{2}}}", r[0], r[1], r[2]);
                if (i < list.Count - 1) sb.Append(",");
            }
            sb.Append("]");
        }

        private void AppendProfile(StringBuilder sb, int maxLevels)
        {
            var items = new List<KeyValuePair<double, long>>(volByPrice);
            items.Sort((a, b) => b.Value.CompareTo(a.Value));
            int n = Math.Min(maxLevels, items.Count);
            for (int i = 0; i < n; i++)
            {
                sb.AppendFormat("\"{0}\":{1}", F(items[i].Key), items[i].Value);
                if (i < n - 1) sb.Append(",");
            }
        }
    }
}
