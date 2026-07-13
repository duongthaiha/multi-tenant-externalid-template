namespace Contoso.AssetManagement.Shared;

public static class TenantConstants
{
    public static class Tenants
    {
        public const string AlphaCapital = "AlphaCapital";
        public const string BetaWealth = "BetaWealth";
        public const string GammaFund = "GammaFund";
        public const string DeltaEquity = "DeltaEquity";

        public static readonly string[] InitialTenants =
        [
            AlphaCapital,
            BetaWealth,
            GammaFund
        ];
    }

    public static class Claims
    {
        public const string TenantId = "extension_tenantId";
        public const string TenantStatus = "tenant_status";
        public const string Scope = "scp";
        public const string Roles = "tenant_roles";
        public const string ObjectId = "oid";
        public const string Subject = "sub";
        public const string Issuer = "iss";
        public const string PreferredUsername = "preferred_username";
    }

    public static class Headers
    {
        public const string ClientForwardedPrefix = "x-client-";
        public const string Authorization = "Authorization";
        public const string CorrelationId = "X-Correlation-ID";
        public const string AuthenticatedTenant = "X-Authenticated-Tenant";
        public const string AuthenticatedUser = "X-Authenticated-User";
        public const string UserAuthorization = "X-User-Authorization";
        public const string ServiceAuthorization = "X-Service-Authorization";
        public const string TenantId = "X-Tenant-Id";
        public const string UserId = "X-User-Id";
        public const string ForwardedUser = "X-Forwarded-User";
        public const string AgentId = "X-Agent-Id";
        public const string AuthorizationDecision = "X-Authorization-Decision";
        public const string FoundryUserIdentity = "x-ms-user-identity";

        public static string ClientForwarded(string headerName) => $"{ClientForwardedPrefix}{headerName}";
    }

    public static class TenantStatus
    {
        public const string Active = "active";
        public const string Suspended = "suspended";
        public const string Inactive = "inactive";
    }

    public static class Scopes
    {
        public const string AssetsRead = "assets.read";
        public const string AssetsWrite = "assets.write";
    }

    public static class Roles
    {
        public const string TenantAdmin = "TenantAdmin";
        public const string PortfolioManager = "PortfolioManager";
        public const string PortfolioViewer = "PortfolioViewer";

        public static readonly string[] AssetReaders =
        [
            TenantAdmin,
            PortfolioManager,
            PortfolioViewer
        ];

        public static readonly string[] AssetWriters =
        [
            TenantAdmin,
            PortfolioManager
        ];
    }

    public static class AuthorizationDecisions
    {
        public const string Allowed = "allowed";
        public const string MissingTenantClaim = "missing-tenant-claim";
        public const string TenantInactive = "tenant-inactive";
        public const string TenantMismatch = "tenant-mismatch";
        public const string MissingScope = "missing-scope";
        public const string MissingRole = "missing-role";
        public const string MissingServiceAuthentication = "missing-service-authentication";
        public const string ResourceTenantMismatch = "resource-tenant-mismatch";
    }
}
