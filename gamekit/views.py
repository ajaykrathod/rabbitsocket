# set async_mode to 'threading', 'eventlet', 'gevent' or 'gevent_uwsgi' to
# force a mode else, the best mode is selected automatically from what's
# installed
async_mode = None

from array import array
from base64 import decode
import collections
import os
from django.dispatch import receiver
from django.shortcuts import render
import socketio
import pika
from marshmallow import Schema, fields
from bson import ObjectId
from pymongo import MongoClient
import environ
import json


# Initialise environment variables
env = environ.Env()
environ.Env.read_env()



class SetEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return json.JSONEncoder.default(self, obj)



class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return json.JSONEncoder.default(self, o)


basedir = os.path.dirname(os.path.realpath(__file__))
sio = socketio.Server(async_mode=async_mode)
thread = None

connection = pika.BlockingConnection(
    pika.ConnectionParameters(host="localhost")
)

channel = connection.channel()
users = set()
messages = []
chatMessages = []
jsonMessages = []


def get_database():

    # Provide the mongodb atlas url to connect python to mongodb using pymongo
    CONNECTION_STRING = "mongodb+srv://"+env('MONGO_USERNAME')+":"+env('MONGO_PASSWORD')+"@realmcluster.th5q0.mongodb.net/?retryWrites=true&w=majority"

    # Create a connection using MongoClient. You can import MongoClient or use pymongo.MongoClient
    client = MongoClient(CONNECTION_STRING)

    # Create the database for our example (we will use the same database throughout the tutorial
    return client['chats']




def index(request):
    global thread
    if thread is None:
        thread = sio.start_background_task(background_thread)
    return render(request,'index.html')


def background_thread():
    """Example of how to send server generated events to clients."""
    count = 0
    while True:
        sio.sleep(10)
        count += 1
        sio.emit('my_response', {'data': 'Server generated event'},
                 namespace='/test')

def callback(response,queue_name,sid,message):
        print(response)
        if response:
            channel = connection.channel()
            q = channel.queue_declare(queue=queue_name, durable=True)
            q_len = q.method.message_count
            if q_len > 0:
                for method_frame, properties, body in channel.consume(queue_name):

                    # Display the message parts

                    # Acknowledge the message
                    channel.basic_ack(method_frame.delivery_tag)
                    # Escape out of the loop after 10 messages
                    if method_frame.delivery_tag == 1:
                        channel.cancel()
                        break

                    if method_frame.delivery_tag >= q_len:
                        channel.stop_consuming()
                        channel.cancel()
                        break
            # channel.queue_declare(queue=queue_name, durable=True)
            # channel.basic_qos(prefetch_count=1)
            # channel.basic_consume(queue=queue_name,on_message_callback=lambda ch, method, properties, body: room_event_callback(ch,method,properties,body,sid,message))
            # channel.start_consuming()

def message_callback(ch, method, properties, body,sid,message):
    recieved = body.decode()
    ch.basic_ack(delivery_tag=method.delivery_tag)
    ch.stop_consuming()
    # sio.emit('my_response', {'data': recieved,'sender':message['sender'],'receiver':message['receiver']},
    #          room=message['room'])
    pass
     
    

def room_event_callback(ch, method, properties, body,sid,message):
    recieved = body.decode()
    ch.basic_ack(delivery_tag=method.delivery_tag)
    ch.stop_consuming()
    # sio.emit('my_response', {'data': recieved,'sender':message['sender'],'receiver':message['receiver']},
    #          room=message['room'])
    pass


@sio.event
def connectionEstablised(sid, message):
    users.add(message['data'])
    dictionary = {}
    for user in users:
        dictionary[user] = user
    sio.emit('connection_response', {'data': dictionary})


# @sio.event
# def my_broadcast_event(sid, message):
#     sio.emit('my_response', {'data': message['data']})


@sio.event
def join(sid, message):
    jsonMessages = []
    messages = []
    chatMessages = []
    dbname = get_database()
    collection = dbname[message['room']]
    item_details = collection.find({},{'_id':0})
    for item in item_details:
        chatMessages.append(item)
    try:
        channel = connection.channel()
        q = channel.queue_declare(queue=message['room'], durable=True)
        q_len = q.method.message_count
        channel.basic_qos(prefetch_count=1)
        if q_len > 0:
            for method_frame, properties, body in channel.consume(message['room']):
                # Display the message parts
                messages.append(body.decode())
                # Acknowledge the message
                channel.basic_ack(method_frame.delivery_tag)
                
                if method_frame.delivery_tag >= q_len:
                    channel.stop_consuming()
                    channel.cancel()
                    break

    except BaseException as exception:
        print("Exception",exception)
    

    
    # tempMsg = []
    # for chat in chatMessages:
    #     msg = JSONEncoder().encode(chat)
    #     tempMsg.append(msg)
    
    sio.enter_room(sid,message['room'])
    if len(messages) > 0:
        for msg in messages:
            loaded_str = json.loads(msg)
            jsonMessages.append(loaded_str)
        collection.insert_many(jsonMessages)
        chatMessages.append(jsonMessages)

    for msg in chatMessages:
        if type(msg) == list:
            msg = msg[0]
        sio.emit('my_response', {'data': msg['data'],'sender':msg['sender'],'receiver':msg['receiver'],'date':msg['data']},
            room=message['room'])
    

@sio.event
def leave(sid, message):
    sio.leave_room(sid, message['room'])
    sio.emit('my_response', {'data': 'Left room: ' + message['room']},
             room=message['room'])


@sio.event
def close_room(sid, message):
    sio.emit('my_response',
             {'data': 'Room ' + message['room'] + ' is closing.'},
             room=message['room'])
    sio.close_room(message['room'])

@sio.event
def my_room_event(sid, message):
    try:
        channel = connection.channel()
        channel.queue_declare(queue=message['room'], durable=True)
        channel.basic_publish(
            exchange='',
            routing_key=message['room'],
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode = pika.spec.PERSISTENT_DELIVERY_MODE
            )
        )
    except pika.exceptions.ConnectionClosedByBroker:
        print("Closed Error",pika.exceptions.ConnectionClosedByBroker)
    # Do not recover on channel errors
    except pika.exceptions.AMQPChannelError:
        print("AMQPChannel Error",pika.exceptions.AMQPChannelError)
    # Recover on all other connection errors
    except pika.exceptions.AMQPConnectionError as connectionError:
        print("Connection Error",connectionError)
    sio.emit('message', {'data': message['data'],'sender':message['sender']}, room=message['room'])


@sio.event
def disconnect_request():
    sio.disconnect()


@sio.event
def connect(sid, environ):
    pass
    # sio.emit('my_response', {'data': 'Connected', 'count': 0}, room=sid)


@sio.event
def disconnect():
    print('Client disconnected')