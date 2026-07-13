using Contoso.AssetManagement.FrontendApi.Agent;

namespace Contoso.AssetManagement.FrontendApi.Tests;

public sealed class InMemoryAgentSessionBindingStoreTests
{
    [Fact]
    public async Task GetOwnedAsync_ReturnsBindingOnlyForMatchingOwnerAndAgent()
    {
        var store = new InMemoryAgentSessionBindingStore();
        var binding = await store.CreateAsync(CreateBinding(), CancellationToken.None);

        var match = await store.GetOwnedAsync(binding.SessionHandle, binding.TenantId, binding.UserId, binding.AgentName, CancellationToken.None);
        var wrongHandle = await store.GetOwnedAsync(new AgentSessionHandle("app-session-other"), binding.TenantId, binding.UserId, binding.AgentName, CancellationToken.None);
        var wrongTenant = await store.GetOwnedAsync(binding.SessionHandle, "BetaWealth", binding.UserId, binding.AgentName, CancellationToken.None);
        var wrongUser = await store.GetOwnedAsync(binding.SessionHandle, binding.TenantId, "user-other", binding.AgentName, CancellationToken.None);
        var wrongAgent = await store.GetOwnedAsync(binding.SessionHandle, binding.TenantId, binding.UserId, "other-agent", CancellationToken.None);

        Assert.Equal(binding, match);
        Assert.Equal("foundry-conversation-1", match?.FoundryConversationId?.Value);
        Assert.Null(wrongHandle);
        Assert.Null(wrongTenant);
        Assert.Null(wrongUser);
        Assert.Null(wrongAgent);
    }

    [Fact]
    public async Task StatusUpdates_OnlyPersistBindingMetadataAndNoTokensOrPrompts()
    {
        var store = new InMemoryAgentSessionBindingStore();
        var created = await store.CreateAsync(CreateBinding(), CancellationToken.None);
        var now = DateTimeOffset.UtcNow;

        var used = await store.MarkUsedAsync(created, now, now.AddHours(4), "corr-used", CancellationToken.None);
        var stopped = await store.MarkStoppedAsync(used, now.AddMinutes(1), "corr-stopped", CancellationToken.None);
        var deleted = await store.MarkDeletedAsync(stopped, now.AddMinutes(2), "corr-deleted", CancellationToken.None);

        Assert.Equal(AgentSessionStatus.Deleted, deleted.Status);
        Assert.Equal("corr-deleted", deleted.CorrelationId);
        Assert.DoesNotContain(typeof(AgentSessionBinding).GetProperties(), property =>
            property.Name.Contains("Token", StringComparison.OrdinalIgnoreCase)
            || property.Name.Contains("Prompt", StringComparison.OrdinalIgnoreCase)
            || property.Name.Contains("Message", StringComparison.OrdinalIgnoreCase));

        var stored = await store.GetOwnedAsync(created.SessionHandle, created.TenantId, created.UserId, created.AgentName, CancellationToken.None);
        Assert.NotNull(stored);
        Assert.Equal(AgentSessionStatus.Deleted, stored.Status);
        Assert.DoesNotContain("secret-user-token", stored.ToString(), StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("sensitive prompt", stored.ToString(), StringComparison.OrdinalIgnoreCase);
    }

    private static AgentSessionBinding CreateBinding() => new(
        new AgentSessionHandle("app-session-1"),
        new FoundryAgentSessionId("foundry-session-1"),
        "AlphaCapital",
        "user-123",
        new FoundryUserIdentity("tenant-alphacapital-user-hash"),
        "portfolio-agent",
        AgentSessionProtocolMode.ResponsesV2Delegated,
        DateTimeOffset.UtcNow.AddMinutes(-1),
        DateTimeOffset.UtcNow.AddMinutes(-1),
        DateTimeOffset.UtcNow.AddHours(1),
        AgentSessionStatus.Active,
        CorrelationId: "corr-create",
        FoundryConversationId: new FoundryConversationId("foundry-conversation-1"));
}
