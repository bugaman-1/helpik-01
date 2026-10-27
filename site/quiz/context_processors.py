from .services.onboarding import enabled


def prototype_features(request):
    return {'prototype_self_service': enabled()}
