from .models import KindergartenSettings, ChatMessage, Child

def kindergarten_context(request):
    settings = KindergartenSettings.get_settings()
    unread_chat_count = 0
    total_active_children = 0
    if request.user.is_authenticated:
        unread_chat_count = ChatMessage.objects.filter(
            is_read=False
        ).exclude(sender=request.user).count()
        total_active_children = Child.objects.filter(is_active=True).count()
        
    return {
        'kindergarten_settings': settings,
        'unread_chat_count': unread_chat_count,
        'total_active_children': total_active_children,
    }
