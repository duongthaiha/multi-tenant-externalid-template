FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY src/shared/Contoso.AssetManagement.Shared.csproj src/shared/
COPY src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj src/frontend-api/
RUN dotnet restore src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj
COPY src/shared/ src/shared/
COPY src/frontend-api/ src/frontend-api/
RUN dotnet publish src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj -c Release -o /app/publish --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app/publish .
EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080
ENTRYPOINT ["dotnet", "Contoso.AssetManagement.FrontendApi.dll"]
