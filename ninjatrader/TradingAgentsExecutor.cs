#region Using declarations
using System;
using System.IO;
using System.Globalization;
using System.ComponentModel.DataAnnotations;
using System.Collections.Generic;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion

// ESTRATEGIA ejecutora para la app TradingAgents.
// Lee data\order_request.json (que escribe la app) y coloca la orden en su cuenta.
//
// MARKET = inmediata.
// LIMIT/STOP = se envia UNA SOLA VEZ con el overload avanzado isLiveUntilCancelled=true,
//   de modo que NO se cancela al cierre de barra y permanece viva hasta llenarse o hasta
//   que la cancelemos por vigencia (vigencia_min velas de 1m). NO se re-envia cada vela
//   (re-enviar con el mismo signalName MODIFICA la orden y, en el borde del fill, la
//   cancela/recrea -> deja el bracket SL/TP huerfano: ese era el bug).
// SL/TP = se arman UNA sola vez ANTES de la entrada con SetStopLoss/SetProfitTarget y el
//   mismo fromEntrySignal que la entrada; NinjaTrader los adjunta como par OCO en cuanto
//   la entrada se llena, sin importar el timing de ticks/velas -> nunca posicion sin stop.
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
        private double startUnix;   // hora (epoch seg) en que la estrategia entro a realtime

        // Estado de la orden LIMIT/STOP pendiente (enviada UNA vez, viva hasta llenarse/vencer).
        private bool pend;
        private string pId = "", pAccion = "", pTipo = "";
        private int pQty;
        private double pPrecio, pSl, pTp;
        private int pExpiraBar;          // barra en la que vence la vigencia si no se lleno
        private Order entryOrder;        // referencia a la orden de entrada (asignada en OnOrderUpdate)
        private string pSignal = "";     // signalName ESTABLE de la entrada/salidas para esta orden
        private bool slTpArmado;         // SL/TP ya armados para la entrada actual

        // Estado de una ESCALERA (ladder): varias LIMIT con SL global + TPs scale-out.
        private bool ladderPend;
        private string ladderId = "";
        private int ladderExpiraBar;
        private readonly List<Order> ladderEntries = new List<Order>();

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name                         = "TradingAgentsExecutor";
                Description                  = "Ejecuta ordenes de la app TradingAgents (MARKET inmediata, LIMIT/STOP viva hasta llenarse, SL/TP OCO garantizado).";
                Calculate                    = Calculate.OnEachTick;
                EntriesPerDirection          = 8;   // soporta ESCALERAS (ladder) de hasta 8 niveles
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                OrderFile                    = @"e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents\data\order_request.json";
                VelasVida                    = 15;
            }
            else if (State == State.Realtime)
            {
                startUnix = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
            }
        }

        protected override void OnBarUpdate()
        {
            if (State != State.Realtime) return;

            // 1) Vigencia de la entrada pendiente: si NO se lleno y la orden sigue Working
            //    cuando se acaba la vigencia, la cancelamos EXPLICITAMENTE (no se cancela sola
            //    porque va con isLiveUntilCancelled). No re-enviamos nada aqui.
            if (pend && CurrentBar >= pExpiraBar)
            {
                if (entryOrder != null && (entryOrder.OrderState == OrderState.Working
                                        || entryOrder.OrderState == OrderState.Accepted
                                        || entryOrder.OrderState == OrderState.Submitted))
                {
                    CancelOrder(entryOrder);
                    // El estado EXPIRADA se confirma en OnOrderUpdate al pasar a Cancelled.
                }
                else if (entryOrder == null)
                {
                    // No tenemos referencia (caso raro): marcamos vencida y limpiamos plantillas.
                    pend = false;
                    ResetSalidas();
                    WriteStatus(pId, "EXPIRADA", "no se lleno la " + pTipo + " a tiempo (vigencia agotada)");
                }
            }

            // 1b) Vigencia de la ESCALERA: cancela los escalones que NO se llenaron.
            //     Los que SI se llenaron conservan su bracket SL/TP.
            if (ladderPend && CurrentBar >= ladderExpiraBar)
            {
                bool any = false;
                foreach (Order o in ladderEntries)
                    if (o != null && (o.OrderState == OrderState.Working
                                   || o.OrderState == OrderState.Accepted
                                   || o.OrderState == OrderState.Submitted))
                    { CancelOrder(o); any = true; }
                ladderPend = false;
                if (any) WriteStatus(ladderId, "EXPIRADA", "escalones no llenados cancelados (vigencia)");
            }

            // 2) Revisar si llego una orden nueva (cada ~2s).
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

                // Ignorar ordenes VIEJAS (creadas antes de arrancar la estrategia).
                double idNum;
                if (double.TryParse(id, NumberStyles.Any, CultureInfo.InvariantCulture, out idNum) && idNum > 0 && idNum < startUnix - 5)
                {
                    lastOrderId = id;
                    return;
                }

                string par = GetStr(json, "par");
                if (!string.IsNullOrEmpty(par) && !string.Equals(par, Instrument.MasterInstrument.Name, StringComparison.OrdinalIgnoreCase))
                {
                    lastOrderId = id;
                    return;   // no es mi instrumento
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
                double vigencia = GetNum(json, "vigencia_min", 0);
                double sl = GetNum(json, "sl", 0);
                double tp = GetNum(json, "tp", 0);
                lastOrderId = id;

                if (accion == "FLAT")
                {
                    // Cancelar cualquier entrada pendiente (single + escalera), cerrar y limpiar.
                    if (entryOrder != null && (entryOrder.OrderState == OrderState.Working
                                            || entryOrder.OrderState == OrderState.Accepted
                                            || entryOrder.OrderState == OrderState.Submitted))
                        CancelOrder(entryOrder);
                    pend = false;
                    entryOrder = null;
                    CancelLadder();
                    if (Position.MarketPosition == MarketPosition.Long) ExitLong();
                    else if (Position.MarketPosition == MarketPosition.Short) ExitShort();
                    ResetSalidas();
                    WriteStatus(id, "EJECUTADA", "FLAT");
                    return;
                }

                if (tipo == "LADDER" && (accion == "LONG" || accion == "SHORT"))
                {
                    // ESCALERA: varias LIMIT escalonadas, SL global unico, TPs scale-out (uno por escalon).
                    CancelLadder();
                    if (pend && entryOrder != null && (entryOrder.OrderState == OrderState.Working
                                                    || entryOrder.OrderState == OrderState.Accepted
                                                    || entryOrder.OrderState == OrderState.Submitted))
                        CancelOrder(entryOrder);
                    pend = false; entryOrder = null;

                    List<double[]> esc = GetEscalones(json);
                    List<double> tps = GetTps(json);
                    if (esc.Count == 0) { WriteStatus(id, "RECHAZADA", "escalera sin escalones validos"); return; }

                    int velasVidaL = vigencia > 0 ? (int)vigencia : VelasVida;
                    if (velasVidaL < 1) velasVidaL = 1;
                    ladderId = id; ladderExpiraBar = CurrentBar + velasVidaL;
                    ladderEntries.Clear(); ladderPend = true;
                    ResetSalidas();

                    int totalQ = 0;
                    for (int i = 0; i < esc.Count; i++)
                    {
                        double precio = esc[i][0];
                        int q = (int)esc[i][1]; if (q < 1) q = 1;
                        totalQ += q;
                        string sig = (accion == "LONG" ? "TA_Long_" : "TA_Short_") + id + "_" + i;
                        if (sl > 0) SetStopLoss(sig, CalculationMode.Price, sl, false);   // SL GLOBAL (mismo precio para todos)
                        double tpi = (i < tps.Count ? tps[i] : (tps.Count > 0 ? tps[tps.Count - 1] : 0));
                        if (tpi > 0) SetProfitTarget(sig, CalculationMode.Price, tpi);    // TP por escalon (scale-out)
                        Order o = (accion == "LONG")
                            ? EnterLongLimit(0, true, q, precio, sig)
                            : EnterShortLimit(0, true, q, precio, sig);
                        ladderEntries.Add(o);
                    }
                    slTpArmado = (sl > 0 || tps.Count > 0);
                    WriteStatus(id, "COLOCADA", accion + " ESCALERA " + esc.Count + " limits (" + totalQ
                        + " micros), SL global " + Num(sl) + " (vigencia " + velasVidaL + " min)");
                    return;
                }

                if ((tipo == "LIMIT" || tipo == "STOP") && entrada > 0 && (accion == "LONG" || accion == "SHORT"))
                {
                    // Si ya hay una entrada pendiente, cancelarla antes de colocar la nueva.
                    if (pend && entryOrder != null && (entryOrder.OrderState == OrderState.Working
                                                    || entryOrder.OrderState == OrderState.Accepted
                                                    || entryOrder.OrderState == OrderState.Submitted))
                        CancelOrder(entryOrder);

                    int velasVida = vigencia > 0 ? (int)vigencia : VelasVida;   // vigencia (min) = velas en grafico 1m
                    if (velasVida < 1) velasVida = 1;                           // nunca expirar en la misma barra del envio

                    pId = id; pAccion = accion; pTipo = tipo; pQty = qty; pPrecio = entrada; pSl = sl; pTp = tp;
                    pSignal = (accion == "LONG" ? "TA_Long_" : "TA_Short_") + id;   // signalName ESTABLE por orden
                    pExpiraBar = CurrentBar + velasVida;
                    entryOrder = null;
                    pend = true;

                    // Enviar la entrada UNA sola vez (con SL/TP armados antes).
                    Submit();

                    WriteStatus(id, "COLOCADA", accion + " " + qty + " " + tipo + " @ " + Num(entrada) + " (vigencia " + velasVida + " min, esperando llenado)");
                }
                else if (accion == "LONG" || accion == "SHORT")
                {
                    // MARKET inmediata. SL/TP armados antes de entrar -> OCO automatico al llenarse.
                    ResetSalidas();
                    pSignal = (accion == "LONG" ? "TA_Long_" : "TA_Short_") + id;
                    if (sl > 0) { SetStopLoss(pSignal, CalculationMode.Price, sl, false); }
                    if (tp > 0) { SetProfitTarget(pSignal, CalculationMode.Price, tp); }
                    slTpArmado = (sl > 0 || tp > 0);
                    if (accion == "LONG") EnterLong(qty, pSignal);
                    else EnterShort(qty, pSignal);
                    pend = false;
                    entryOrder = null;
                    WriteStatus(id, "EJECUTADA", accion + " " + qty + " MARKET");
                }
            }
            catch (Exception ex) { Print("TradingAgentsExecutor error: " + ex.Message); }
        }

        // Envia la entrada LIMIT/STOP UNA sola vez:
        //  - arma SL/TP ANTES (asociados por fromEntrySignal = pSignal) para que NinjaTrader
        //    los adjunte como OCO en cuanto la entrada se llene (timing-independiente).
        //  - usa el overload avanzado isLiveUntilCancelled=true -> la orden NO expira al
        //    cierre de barra; vive hasta llenarse o hasta que la cancelemos por vigencia.
        private void Submit()
        {
            ResetSalidas();
            if (pSl > 0) { SetStopLoss(pSignal, CalculationMode.Price, pSl, false); }
            if (pTp > 0) { SetProfitTarget(pSignal, CalculationMode.Price, pTp); }
            slTpArmado = (pSl > 0 || pTp > 0);

            if (pAccion == "LONG")
            {
                if (pTipo == "STOP") entryOrder = EnterLongStopMarket(0, true, pQty, pPrecio, pSignal);
                else                 entryOrder = EnterLongLimit(0, true, pQty, pPrecio, pSignal);
            }
            else
            {
                if (pTipo == "STOP") entryOrder = EnterShortStopMarket(0, true, pQty, pPrecio, pSignal);
                else                 entryOrder = EnterShortLimit(0, true, pQty, pPrecio, pSignal);
            }
        }

        // Resetea las plantillas SetStopLoss/SetProfitTarget para que la PROXIMA entrada
        // NO herede niveles viejos (los Set conservan su valor entre entradas en managed).
        private void ResetSalidas()
        {
            if (!slTpArmado) return;
            try
            {
                SetStopLoss(CalculationMode.Price, 0);
                SetProfitTarget(CalculationMode.Price, 0);
            }
            catch { }
            slTpArmado = false;
        }

        // Seguimiento fiable del ciclo de vida de la entrada (la doc recomienda asignar/
        // verificar la Order aqui, NO en OnBarUpdate justo tras el Submit).
        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
            int quantity, int filled, double averageFillPrice, OrderState orderState, DateTime time,
            ErrorCode error, string nativeError)
        {
            if (string.IsNullOrEmpty(pSignal) || order == null) return;
            if (order.FromEntrySignal != pSignal) return;   // solo la entrada de esta orden

            // Asegurar/refrescar la referencia a la orden de entrada.
            entryOrder = order;

            if (orderState == OrderState.Cancelled && pend)
            {
                // Cancelacion por vigencia (o cancelacion previa al reemplazo): confirmar EXPIRADA
                // solo si NO se lleno (sin posicion en la direccion esperada).
                bool lleno = (pAccion == "LONG"  && Position.MarketPosition == MarketPosition.Long)
                          || (pAccion == "SHORT" && Position.MarketPosition == MarketPosition.Short);
                if (!lleno && filled == 0)
                {
                    pend = false;
                    entryOrder = null;
                    ResetSalidas();
                    WriteStatus(pId, "EXPIRADA", "no se lleno la " + pTipo + " a tiempo (vigencia agotada)");
                }
            }
            else if (orderState == OrderState.Rejected)
            {
                pend = false;
                entryOrder = null;
                ResetSalidas();
                WriteStatus(pId, "RECHAZADA", "entrada " + pTipo + " rechazada: " + nativeError);
            }
        }

        // Deteccion robusta del LLENADO: cuando la EJECUCION corresponde a la orden de entrada
        // y ya esta Filled, el bracket SL/TP (armado antes) queda adjuntado como OCO a la
        // posicion real. Reportamos LLENADA aqui (no por Position en OnBarUpdate).
        protected override void OnExecutionUpdate(Execution execution, string executionId, double price,
            int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (string.IsNullOrEmpty(pSignal) || execution == null || execution.Order == null) return;
            if (execution.Order.FromEntrySignal != pSignal) return;

            if (pend && execution.Order.OrderState == OrderState.Filled)
            {
                pend = false;
                WriteStatus(pId, "LLENADA", pAccion + " " + execution.Order.Filled + " @ " + Num(price)
                    + " (SL=" + Num(pSl) + " TP=" + Num(pTp) + " OCO)");
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

        // Formatea numeros SIEMPRE con punto decimal (InvariantCulture) para no romper el
        // JSON del lado app en culturas con coma decimal (es-ES/es-MX).
        private string Num(double v)
        {
            return v.ToString(CultureInfo.InvariantCulture);
        }

        // Cancela los escalones de la escalera que sigan vivos y limpia el estado.
        private void CancelLadder()
        {
            foreach (Order o in ladderEntries)
                if (o != null && (o.OrderState == OrderState.Working
                               || o.OrderState == OrderState.Accepted
                               || o.OrderState == OrderState.Submitted))
                    CancelOrder(o);
            ladderEntries.Clear();
            ladderPend = false;
        }

        // Parsea "escalones":[{"precio":X,"qty":N},...] -> lista de [precio, qty].
        private List<double[]> GetEscalones(string json)
        {
            var list = new List<double[]>();
            int k = json.IndexOf("\"escalones\"");
            if (k < 0) return list;
            int ini = json.IndexOf('[', k);
            if (ini < 0) return list;
            int fin = json.IndexOf(']', ini);
            if (fin < 0 || fin <= ini) return list;
            string arr = json.Substring(ini + 1, fin - ini - 1);
            int p = 0;
            while (true)
            {
                int a = arr.IndexOf('{', p);
                if (a < 0) break;
                int b = arr.IndexOf('}', a);
                if (b < 0) break;
                string obj = arr.Substring(a, b - a + 1);
                double precio = GetNum(obj, "precio", 0);
                double q = GetNum(obj, "qty", 1);
                if (precio > 0) list.Add(new double[] { precio, q });
                p = b + 1;
            }
            return list;
        }

        // Parsea "tps":[a,b,c] -> lista de numeros (TPs scale-out).
        private List<double> GetTps(string json)
        {
            var list = new List<double>();
            int k = json.IndexOf("\"tps\"");
            if (k < 0) return list;
            int ini = json.IndexOf('[', k);
            if (ini < 0) return list;
            int fin = json.IndexOf(']', ini);
            if (fin < 0 || fin <= ini) return list;
            string arr = json.Substring(ini + 1, fin - ini - 1);
            foreach (string part in arr.Split(','))
            {
                double v;
                if (double.TryParse(part.Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out v) && v > 0)
                    list.Add(v);
            }
            return list;
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
