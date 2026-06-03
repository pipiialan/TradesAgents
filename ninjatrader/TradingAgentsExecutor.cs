#region Using declarations
using System;
using System.IO;
using System.Globalization;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion

// ESTRATEGIA ejecutora para la app TradingAgents.
// Lee data\order_request.json (que escribe la app al pulsar "Ejecutar") y coloca la orden
// de mercado con su SL/TP en la cuenta donde esta HABILITADA esta estrategia.
//
// SEGURIDAD: solo ejecuta si el campo "cuenta" de la orden COINCIDE con la cuenta de la
// estrategia. Asi, si la app pide Sim101 pero la habilitaste en otra cuenta, la rechaza.
// Recomendado: habilitar SIEMPRE primero en Sim101 para probar.
namespace NinjaTrader.NinjaScript.Strategies
{
    public class TradingAgentsExecutor : Strategy
    {
        [NinjaScriptProperty]
        [Display(Name = "Archivo de orden", Order = 1, GroupName = "Parameters")]
        public string OrderFile { get; set; }

        private string lastOrderId = "";
        private DateTime lastCheck = DateTime.MinValue;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name                  = "TradingAgentsExecutor";
                Description           = "Ejecuta ordenes enviadas por la app TradingAgents (order_request.json).";
                Calculate             = Calculate.OnEachTick;
                EntriesPerDirection   = 1;
                EntryHandling         = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                OrderFile             = @"e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents\data\order_request.json";
            }
        }

        protected override void OnBarUpdate()
        {
            if (State != State.Realtime) return;                 // nunca en historico
            if ((DateTime.Now - lastCheck).TotalSeconds < 2) return;  // revisar cada ~2s
            lastCheck = DateTime.Now;
            CheckOrder();
        }

        private void CheckOrder()
        {
            try
            {
                if (!File.Exists(OrderFile)) return;
                string json = File.ReadAllText(OrderFile);

                string id = GetStr(json, "id");
                if (string.IsNullOrEmpty(id) || id == lastOrderId) return;  // ya procesada

                // Solo proceso ordenes para MI instrumento (permite un ejecutor por par: MNQ, MGC, MES...).
                string par = GetStr(json, "par");
                string miInstr = Instrument.MasterInstrument.Name;
                if (!string.IsNullOrEmpty(par) && !string.Equals(par, miInstr, StringComparison.OrdinalIgnoreCase))
                {
                    lastOrderId = id;   // vista por esta instancia; la del par correcto la ejecutara
                    return;
                }

                string cuenta = GetStr(json, "cuenta");
                if (!string.Equals(cuenta, Account.Name, StringComparison.OrdinalIgnoreCase))
                {
                    lastOrderId = id;
                    WriteStatus(id, "RECHAZADA", "Cuenta de la orden (" + cuenta + ") != cuenta de la estrategia (" + Account.Name + ")");
                    Print("TradingAgentsExecutor: orden RECHAZADA por cuenta distinta.");
                    return;
                }

                string accion = GetStr(json, "accion");
                int qty = (int)GetNum(json, "qty", 1);
                double sl = GetNum(json, "sl", 0);
                double tp = GetNum(json, "tp", 0);
                lastOrderId = id;

                if (sl > 0) SetStopLoss(CalculationMode.Price, sl);
                if (tp > 0) SetProfitTarget(CalculationMode.Price, tp);

                if (accion == "LONG")
                {
                    EnterLong(qty, "TA_Long");
                    WriteStatus(id, "EJECUTADA", "LONG " + qty + " @ market");
                }
                else if (accion == "SHORT")
                {
                    EnterShort(qty, "TA_Short");
                    WriteStatus(id, "EJECUTADA", "SHORT " + qty + " @ market");
                }
                else if (accion == "FLAT")
                {
                    if (Position.MarketPosition == MarketPosition.Long) ExitLong();
                    else if (Position.MarketPosition == MarketPosition.Short) ExitShort();
                    WriteStatus(id, "EJECUTADA", "FLAT");
                }

                Print("TradingAgentsExecutor: " + accion + " " + qty + " en " + Account.Name);
            }
            catch (Exception ex)
            {
                Print("TradingAgentsExecutor error: " + ex.Message);
            }
        }

        private void WriteStatus(string id, string estado, string detalle)
        {
            try
            {
                string dir = Path.GetDirectoryName(OrderFile);
                string s = "{\"id\":\"" + id + "\",\"estado\":\"" + estado + "\",\"detalle\":\"" + detalle.Replace("\"", "'") + "\",\"cuenta\":\"" + Account.Name + "\"}";
                File.WriteAllText(Path.Combine(dir, "order_status.json"), s);
            }
            catch { }
        }

        // --- parseo JSON simple para el formato fijo de order_request.json ---
        private string GetStr(string json, string key)
        {
            string pat = "\"" + key + "\"";
            int i = json.IndexOf(pat);
            if (i < 0) return "";
            i = json.IndexOf(':', i + pat.Length);
            if (i < 0) return "";
            i++;
            while (i < json.Length && (json[i] == ' ' || json[i] == '\t' || json[i] == '\n' || json[i] == '\r')) i++;
            if (i < json.Length && json[i] == '"')
            {
                int j = json.IndexOf('"', i + 1);
                if (j < 0) return "";
                return json.Substring(i + 1, j - i - 1);
            }
            return "";
        }

        private double GetNum(string json, string key, double def)
        {
            string pat = "\"" + key + "\"";
            int i = json.IndexOf(pat);
            if (i < 0) return def;
            i = json.IndexOf(':', i + pat.Length);
            if (i < 0) return def;
            i++;
            while (i < json.Length && (json[i] == ' ' || json[i] == '\t' || json[i] == '\n' || json[i] == '\r')) i++;
            int s = i;
            while (i < json.Length && (char.IsDigit(json[i]) || json[i] == '.' || json[i] == '-')) i++;
            string num = json.Substring(s, i - s);
            double v;
            if (double.TryParse(num, NumberStyles.Any, CultureInfo.InvariantCulture, out v)) return v;
            return def;
        }
    }
}
