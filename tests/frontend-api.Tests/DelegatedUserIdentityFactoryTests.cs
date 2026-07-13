using System.Security.Claims;
using Contoso.AssetManagement.Shared;
using Contoso.AssetManagement.Shared.Auth;

namespace Contoso.AssetManagement.FrontendApi.Tests;

public sealed class DelegatedUserIdentityFactoryTests
{
    [Fact]
    public void FromValidatedClaims_IsDeterministicForSameTenantAndUser()
    {
        var principal = CreatePrincipal("https://login.contoso.example/issuer", "user-123");

        var first = DelegatedUserIdentityFactory.FromValidatedClaims(principal, TenantConstants.Tenants.AlphaCapital);
        var second = DelegatedUserIdentityFactory.FromValidatedClaims(principal, TenantConstants.Tenants.AlphaCapital);

        Assert.Equal(first, second);
        Assert.StartsWith("user-", first.AppUserId, StringComparison.Ordinal);
        Assert.StartsWith("tenant-alphacapital-user-", first.FoundryUserIdentity, StringComparison.Ordinal);
    }

    [Fact]
    public void FromValidatedClaims_IsScopedByTenantAndUser()
    {
        var alphaUser = DelegatedUserIdentityFactory.FromValidatedClaims(
            CreatePrincipal("https://login.contoso.example/issuer", "user-123"),
            TenantConstants.Tenants.AlphaCapital);
        var betaUser = DelegatedUserIdentityFactory.FromValidatedClaims(
            CreatePrincipal("https://login.contoso.example/issuer", "user-123"),
            TenantConstants.Tenants.BetaWealth);
        var alphaOtherUser = DelegatedUserIdentityFactory.FromValidatedClaims(
            CreatePrincipal("https://login.contoso.example/issuer", "user-456"),
            TenantConstants.Tenants.AlphaCapital);

        Assert.NotEqual(alphaUser.FoundryUserIdentity, betaUser.FoundryUserIdentity);
        Assert.NotEqual(alphaUser.AppUserId, alphaOtherUser.AppUserId);
        Assert.NotEqual(alphaUser.FoundryUserIdentity, alphaOtherUser.FoundryUserIdentity);
    }

    [Fact]
    public void FromValidatedClaims_NormalizesToAllowedFoundryCharacters()
    {
        var identity = DelegatedUserIdentityFactory.FromValidatedClaims(
            CreatePrincipal("https://login.contoso.example/issuer", "user-123"),
            " Alpha Capital / VIP! ");

        Assert.True(DelegatedUserIdentityFactory.IsFoundryUserIdentity(identity.FoundryUserIdentity));
        Assert.Matches("^[A-Za-z0-9._:@-]{1,256}$", identity.FoundryUserIdentity);
        Assert.DoesNotContain("/", identity.FoundryUserIdentity, StringComparison.Ordinal);
        Assert.DoesNotContain("!", identity.FoundryUserIdentity, StringComparison.Ordinal);
        Assert.StartsWith("tenant-alpha-capital---vip-user-", identity.FoundryUserIdentity, StringComparison.Ordinal);
    }

    private static ClaimsPrincipal CreatePrincipal(string issuer, string objectId)
    {
        var claims = new[]
        {
            new Claim(TenantConstants.Claims.Issuer, issuer),
            new Claim(TenantConstants.Claims.ObjectId, objectId)
        };
        return new ClaimsPrincipal(new ClaimsIdentity(claims, authenticationType: "Test"));
    }
}
