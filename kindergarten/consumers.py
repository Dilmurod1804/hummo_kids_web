import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from .models import ChatMessage, User

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs'].get('room_name', 'general')
        self.room_group_name = f"chat_{self.room_name}"

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'chat_message')

            if message_type == 'chat_message':
                message = data.get('message', '').strip()
                user = self.scope["user"]

                if message and user.is_authenticated:
                    # Save message to DB
                    saved_msg = await self.save_message(user, self.room_name, message)

                    # Broadcast to room group
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'chat_broadcast',
                            'id': saved_msg.id,
                            'message': saved_msg.message,
                            'sender_id': user.id,
                            'sender_name': user.get_full_name() or user.username,
                            'sender_role': user.get_role_display(),
                            'timestamp': saved_msg.created_at.strftime('%H:%M'),
                        }
                    )
            elif message_type == 'typing':
                user = self.scope["user"]
                if user.is_authenticated:
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'user_typing',
                            'sender_name': user.get_full_name() or user.username,
                            'is_typing': data.get('is_typing', True)
                        }
                    )
        except Exception as e:
            print("Chat error:", e)

    async def chat_broadcast(self, event):
        # Send message to WebSocket client
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'id': event['id'],
            'message': event['message'],
            'sender_id': event['sender_id'],
            'sender_name': event['sender_name'],
            'sender_role': event['sender_role'],
            'timestamp': event['timestamp'],
        }))

    async def user_typing(self, event):
        await self.send(text_data=json.dumps({
            'type': 'typing',
            'sender_name': event['sender_name'],
            'is_typing': event['is_typing']
        }))

    @database_sync_to_async
    def save_message(self, user, room_name, message):
        return ChatMessage.objects.create(
            sender=user,
            room_name=room_name,
            message=message,
            created_at=timezone.now()
        )
