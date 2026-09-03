using Microsoft.Extensions.Options;

namespace TallyAgent.Modules;

/// <summary>
/// Module #2 (design target) — automated WhatsApp invoice/reminder delivery
/// for Tally-only shops (shops that run Tally but are NOT Metal ERP
/// customers; Metal ERP customers already get this via the server-side
/// Meta Cloud API integration triggered on invoice finalize).
///
/// This module exists mainly to prove out the plugin boundary and the
/// Gateway-reachability/queue handling every future Gateway-based module
/// needs: the Tally XML Gateway only answers while TallyPrime is open with
/// a company loaded, so this is never "always on" — it is reachable during
/// business hours and unreachable otherwise, and both are normal states,
/// not errors.
///
/// The actual send logic (detecting a new voucher via Export, formatting
/// the WhatsApp template call, calling the WhatsApp Business API or
/// forwarding to the backend to reuse app/services/whatsapp.py's send path)
/// is intentionally NOT implemented yet — see the plan's "explicitly out of
/// scope" list. What's here is the scaffolding: reachability check, local
/// outbox for when Tally is closed, and backend outbox drain for when it's
/// open, so a later pass only needs to fill in the actual send call.
/// </summary>
public sealed class WhatsAppDeliveryModule(
    IOptions<AgentOptions> options,
    TallyAgent.Tally.TallyGatewayClient gateway) : IAgentModule
{
    private readonly WhatsAppDeliveryOptions? _opts = options.Value.WhatsAppDelivery;

    public string Name => "whatsapp_delivery";
    public TimeSpan PollInterval => TimeSpan.FromMinutes(_opts?.PollIntervalMinutes ?? 5);

    public async Task RunOnceAsync(AgentContext ctx, CancellationToken ct)
    {
        var log = ctx.CreateLogger("WhatsAppDelivery");
        if (_opts is null || !_opts.Enabled)
        {
            ctx.ReportModuleStatus(Name, "disabled");
            return;
        }

        var reachable = await gateway.IsReachableAsync(_opts.TallyGatewayBaseUrl, ct);
        if (!reachable)
        {
            // Expected, not an error — Tally simply isn't open right now.
            ctx.ReportModuleStatus(Name, "tally_not_open");
            log.LogDebug("Tally Gateway unreachable at {Url} — will retry next poll",
                _opts.TallyGatewayBaseUrl);
            return;
        }

        // Gateway is up: this is where a future pass drains the local
        // outbox (queued while Tally was closed) plus any queued
        // agent_outbox_item rows pulled from the backend's last checkin,
        // and actually sends. Not implemented yet — see class doc.
        ctx.ReportModuleStatus(Name, "ok");
    }
}
