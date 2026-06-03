#region Using declarations
using System;
using System.IO;
using System.Text;
using System.Globalization;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

// Indicador puente para la app TradingAgents.
// Exporta contexto multi-TF (1m + 5m/15m/1h + niveles diarios) a un JSON que lee la app en Python.
// Ponlo en un gráfico de 1 minuto del instrumento que quieras analizar (NQ, GC, etc.).
namespace NinjaTrader.NinjaScript.Indicators
{
    public class TradingAgentsExporter : Indicator
    {
        [NinjaScriptProperty]
        [Display(Name = "Carpeta de exportación", Order = 1, GroupName = "Parameters")]
        public string ExportFolder { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "TradingAgentsExporter";
                Description = "Exporta contexto multi-TF a un JSON para la app TradingAgents.";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = true;
                ExportFolder = @"e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents\data";
            }
            else if (State == State.Configure)
            {
                // Series adicionales (el gráfico 1m es la serie 0).
                AddDataSeries(BarsPeriodType.Minute, 5);   // index 1
                AddDataSeries(BarsPeriodType.Minute, 15);  // index 2
                AddDataSeries(BarsPeriodType.Minute, 60);  // index 3
                AddDataSeries(BarsPeriodType.Day, 1);      // index 4
            }
        }

        protected override void OnBarUpdate()
        {
            // Solo en la serie primaria (1m) y solo cuando hay datos en vivo.
            if (BarsInProgress != 0) return;
            if (State != State.Realtime) return;

            // Necesitamos al menos algo de historia (los arreglos usan Math.Min).
            if (CurrentBars[0] < 1) return;
            if (CurrentBars[1] < 0 || CurrentBars[2] < 0 || CurrentBars[3] < 0) return;
            if (CurrentBars[4] < 1) return;

            try
            {
                string json = BuildJson();
                Directory.CreateDirectory(ExportFolder);
                string file = Path.Combine(ExportFolder, "live_" + Instrument.MasterInstrument.Name + ".json");
                File.WriteAllText(file, json);
            }
            catch (Exception ex)
            {
                Print("TradingAgentsExporter error: " + ex.Message);
            }
        }

        private static string F(double v)
        {
            return v.ToString(CultureInfo.InvariantCulture);
        }

        private string BuildJson()
        {
            var sb = new StringBuilder();
            sb.Append("{");
            sb.AppendFormat("\"par\":\"{0}\",", Instrument.MasterInstrument.Name);
            sb.AppendFormat("\"timestamp\":\"{0}\",", Times[0][0].ToString("yyyy-MM-ddTHH:mm:ss"));
            sb.AppendFormat("\"precio_actual\":{0},", F(Closes[0][0]));

            // Ventana generosa por TF (la app recorta segun el modo scalping/intradia).
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
            sb.Append("}");

            sb.Append("}");
            return sb.ToString();
        }

        // Arreglo JSON de hasta maxBars velas (de la mas vieja a la mas nueva) de la serie seriesIdx.
        private void AppendBars(StringBuilder sb, int seriesIdx, int maxBars)
        {
            sb.Append("[");
            int avail = CurrentBars[seriesIdx] + 1;
            int n = Math.Min(maxBars, avail);
            for (int i = n - 1; i >= 0; i--)
            {
                sb.AppendFormat("{{\"o\":{0},\"h\":{1},\"l\":{2},\"c\":{3},\"v\":{4}}}",
                    F(Opens[seriesIdx][i]), F(Highs[seriesIdx][i]), F(Lows[seriesIdx][i]), F(Closes[seriesIdx][i]), F(Volumes[seriesIdx][i]));
                if (i > 0) sb.Append(",");
            }
            sb.Append("]");
        }
    }
}
