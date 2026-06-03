#region Using declarations
using System;
using System.IO;
using System.Globalization;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion

// ESTRATEGIA ejecutora para la app TradingAgents.
// Lee data\order_request.json (que escribe la app) y coloca la orden en su cuenta.
// MARKET = inmediata. LIMIT/STOP = se mantiene viva (re-enviada cada vela) hasta
// llenarse o expirar (~15 velas), porque NinjaTrader cancela las limit administradas
// al cierre de cada vela si no se re-envian. SL/TP se adjuntan al llenarse la entrada.
// SEGURIDAD: solo ejecuta si la cuenta de la orden coincide con la de la estrategia.
// La ruta default apunta a la PC de casa; ajustala en las propiedades segun la PC.
namespace NinjaTrader.NinjaScript.Strategies
{
    public class TradingAgentsExecutor : Strategy
    {
        [NinjaScriptProperty]
        [Display(Name = "Archivo de orden", Order = 1, GroupName = "Parameters")]
        public string OrderFile { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Velas que vive la LIMIT", Order = 2, GroupName = "Parameters")]
        public int VelasVida { get; set; }

        private string lastOrderId = "";
        private DateTime lastCheck = DateTime.MinValue;

        private bool pend;
        private string pId = "", pAccion = "", pTipo = "";
        private int pQty;
        private double pPrecio, pSl, pTp;
        private int pExpiraBar, pUltimaBar;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name                         = "TradingAgentsExecutor";
                Description                  = "Ejecuta ordenes de la app TradingAgents (MARKET inmediata, LIMIT/STOP viva hasta llenarse).";
                Calculate                    = Calculate.OnEachTick;
                EntriesPerDirection          = 1;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                OrderFile                    = @"e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents\data\order_request.json";
                VelasVida                    = 15;
            }
        }

        protected override void OnBarUpdate()
        {
            if (State != State.Realtime) return;

            if (pend)
            {
                bool lleno = (pAccion == "LONG"  && Position.MarketPosition == MarketPosition.Long)
                          || (pAccion == "SHORT" && Position.MarketPosition == MarketPosition.Short);
                if (lleno)
                {
                    pend = false;
                    WriteStatus(pId, "LLENADA", pAccion + " " + pQty + " @ " + pPrecio);
                }
                else if (CurrentBar >= pExpiraBar)
                {
                    pend = false;
                    WriteStatus(pId, "EXPIRADA", "no se lleno la " + pTipo + " en " + VelasVida + " velas");
                }
                else if (CurrentBar != pUltimaBar)
                {
                    pUltimaBar = CurrentBar;
                    Submit();
                }
            }

            if ((DateTime.Now - lastCheck).TotalSeconds < 2) return;
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
                if (string.IsNullOrEmpty(id) || id == lastOrderId) return;

                string par = GetStr(json, "par");
                if (!string.IsNullOrEmpty(par) && !string.Equals(par, Instrument.MasterInstrument.Name, StringComparison.OrdinalIgnoreCase))
                {
                    lastOrderId = id;
                    return;
                }

                string cuenta = GetStr(json, "cuenta");
                if (!string.Equals(cuenta, Account.Name, StringComparison.OrdinalIgnoreCase))
                {
                    lastOrderId = id;
                    WriteStatus(id, "RECHAZADA", "Cuenta de la orden (" + cuenta + ") != cuenta de la estrategia (" + Account.Name + ")");
                    return;
                }

                string accion = GetStr(json, "accion");
                string tipo = GetStr(json, "tipo");
                int qty = (int)GetNum(json, "qty", 1);
                double entrada = GetNum(json, "entrada", 0);
                double sl = GetNum(json, "sl", 0);
                double tp = GetNum(json, "tp", 0);
                lastOrderId = id;

                if (accion == "FLAT")
                {
                    pend = false;
                    if (Position.MarketPosition == MarketPosition.Long) ExitLong();
                    else if (Position.MarketPosition == MarketPosition.Short) ExitShort();
                    WriteStatus(id, "EJECUTADA", "FLAT");
                    return;
                }

                if ((tipo == "LIMIT" || tipo == "STOP") && entrada > 0 && (accion == "LONG" || accion == "SHORT"))
                {
                    pId = id; pAccion = accion; pTipo = tipo; pQty = qty; pPrecio = entrada; pSl = sl; pTp = tp;
                    pExpiraBar = CurrentBar + VelasVida;
                    pUltimaBar = -1;
                    pend = true;
                    Submit();
                    WriteStatus(id, "COLOCADA", accion + " " + qty + " " + tipo + " @ " + entrada + " (esperando llenado)");
                }
                else if (accion == "LONG" || accion == "SHORT")
                {
                    if (sl > 0) SetStopLoss(CalculationMode.Price, sl);
                    if (tp > 0) SetProfitTarget(CalculationMode.Price, tp);
                    if (accion == "LONG") EnterLong(qty, "TA_Long");
                    else EnterShort(qty, "TA_Short");
                    pend = false;
                    WriteStatus(id, "EJECUTADA", accion + " " + qty + " MARKET");
                }
            }
            catch (Exception ex) { Print("TradingAgentsExecutor error: " + ex.Message); }
        }

        private void Submit()
        {
            if (pSl > 0) SetStopLoss(CalculationMode.Price, pSl);
            if (pTp > 0) SetProfitTarget(CalculationMode.Price, pTp);
            if (pAccion == "LONG")
            {
                if (pTipo == "STOP") EnterLongStopMarket(pQty, pPrecio, "TA_Long");
                else EnterLongLimit(pQty, pPrecio, "TA_Long");
            }
            else
            {
                if (pTipo == "STOP") EnterShortStopMarket(pQty, pPrecio, "TA_Short");
                else EnterShortLimit(pQty, pPrecio, "TA_Short");
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
