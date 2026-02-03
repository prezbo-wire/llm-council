using AlexBot.Configuration;
using AlexBot.Services;
using Microsoft.Bot.Builder;
using Microsoft.Bot.Schema;
using Microsoft.Extensions.Options;

namespace AlexBot.Bots;

public class AlexBot : ActivityHandler
{
    private readonly LlmService _llm;
    private readonly AlexOptions _options;

    public AlexBot(LlmService llm, IOptions<AlexOptions> options)
    {
        _llm = llm;
        _options = options.Value;
    }

    protected override async Task OnMessageActivityAsync(ITurnContext<IMessageActivity> turnContext, CancellationToken cancellationToken)
    {
        var text = turnContext.Activity.Text?.Trim();
        if (string.IsNullOrEmpty(text))
        {
            await turnContext.SendActivityAsync(MessageFactory.Text("I did not catch that. Please try again."), cancellationToken);
            return;
        }

        var reply = await _llm.GetReplyAsync(text, cancellationToken);
        await turnContext.SendActivityAsync(MessageFactory.Text(reply), cancellationToken);
    }

    protected override Task OnMembersAddedAsync(IList<ChannelAccount> membersAdded, ITurnContext<IConversationUpdateActivity> turnContext, CancellationToken cancellationToken)
    {
        var welcome = $"Hi, I am {(_options.PersonaName ?? "Alex")}. Add me to a meeting or ping me in chat when you want help.";
        return turnContext.SendActivityAsync(MessageFactory.Text(welcome), cancellationToken);
    }
}
