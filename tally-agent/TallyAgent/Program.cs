using Microsoft.Extensions.Options;
using Serilog;
using TallyAgent;
using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.State;
using TallyAgent.Tally;

var builder = Host.CreateApplicationBuilder(args);

builder.Services.Configure<AgentOptions>(builder.Configuration.GetSection(AgentOptions.SectionName));

// Serilog to a rolling file under the configured LogDirectory, so a shop
// visit / remote session can diagnose "why didn't this file upload"
// without needing the central admin API.
var agentOptions = builder.Configuration.GetSection(AgentOptions.SectionName).Get<AgentOptions>()
    ?? new AgentOptions();
Directory.CreateDirectory(agentOptions.LogDirectory);
Log.Logger = new LoggerConfiguration()
    .MinimumLevel.Information()
    .WriteTo.File(
        Path.Combine(agentOptions.LogDirectory, "tally-agent-.log"),
        rollingInterval: RollingInterval.Day,
        retainedFileCountLimit: 30)
    .CreateLogger();
builder.Services.AddSerilog();

builder.Services.AddSingleton<AgentStateStore>();
builder.Services.AddHttpClient<BackendClient>();
builder.Services.AddHttpClient<TallyGatewayClient>();

builder.Services.AddSingleton<IAgentModule, BackupSyncModule>();
builder.Services.AddSingleton<IAgentModule, BackupHealthMonitorModule>();
builder.Services.AddSingleton<IAgentModule, WhatsAppDeliveryModule>();

builder.Services.AddHostedService<TallyAgentService>();

// Runs at boot with no login session when installed as a Windows Service
// (see install.ps1); falls back to a normal console app under `dotnet run`.
builder.Services.AddWindowsService(o => o.ServiceName = "TallyAgent");

var host = builder.Build();
host.Run();
