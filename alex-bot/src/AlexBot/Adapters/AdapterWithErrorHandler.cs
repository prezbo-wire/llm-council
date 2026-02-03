using Microsoft.Bot.Builder.Integration.AspNet.Core;
using Microsoft.Bot.Builder.TraceExtensions;
using Microsoft.Bot.Builder;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace AlexBot.Adapters;

public class AdapterWithErrorHandler : BotFrameworkHttpAdapter
{
    public AdapterWithErrorHandler(IConfiguration configuration, ILogger<BotFrameworkHttpAdapter> logger)
        : base(configuration, logger)
    {
        OnTurnError = async (turnContext, exception) =>
        {
            logger.LogError(exception, "Bot error: {Message}", exception.Message);
            await turnContext.SendActivityAsync("Sorry, something went wrong.");
            await turnContext.TraceActivityAsync("OnTurnError Trace", exception.Message);
        };
    }
}
