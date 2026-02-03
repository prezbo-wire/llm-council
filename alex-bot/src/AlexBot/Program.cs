using AlexBot.Adapters;
using AlexBot.Configuration;
using AlexBot.Services;
using Microsoft.AspNetCore.Builder;
using Microsoft.Bot.Builder;
using Microsoft.Bot.Builder.Integration.AspNet.Core;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers().AddNewtonsoftJson();
builder.Services.Configure<AlexOptions>(builder.Configuration.GetSection("Alex"));

// Core bot services
builder.Services.AddSingleton<IBotFrameworkHttpAdapter, AdapterWithErrorHandler>();
builder.Services.AddSingleton<IBot, Bots.AlexBot>();

// Domain services
builder.Services.AddSingleton<LlmService>();
builder.Services.AddSingleton<SpeechService>();
builder.Services.AddSingleton<MeetingMediaRouter>();

// Basic API explorer for local smoke tests
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseRouting();
app.UseAuthorization();
app.MapControllers();

app.Run();
