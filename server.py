#!/usr/bin/env python
# -*- coding: utf-8 -*-
import gevent
import gevent.monkey
import uuid
import json
import socket
from gevent.queue import Queue
from gevent.coros import BoundedSemaphore
from gevent.pool import Pool

gevent.monkey.patch_all()

undistri_queue = Queue()
distri_dict = {}
msgQ = Queue()
pool = Pool(100)

ip = '107.170.234.171'
port = 8887
CHAT, INIT, DSTB, TEST = 0, 1, 2, 3, 
CHECKOUT, MISS = -2, -1

listenSoc = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
listenSoc.bind((ip, port))
listenSoc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 10)
listenSoc.listen(1000)

def debug(s):
    print 'Debug:'+s

def sendJSON(signal, toid='', JSON=None, msg='', sendid='', recvid='', recvcon=None):
    '''
    打包json.并发送给接收socket
    成功返回True,失败返回False
    不抛出异常

    signal:int 
    msg:unicode str
    sendid:str
    recvid:str
    '''
    try:
        jsonpkg = None
        if not JSON:
            jsonpkg = json.dumps([signal, msg, sendid, recvid]) 
        else:
            jsonpkg = JSON
        if not recvcon and toid:
            objid, recvcon, gl = distri_dict[toid]
        recvcon.sendall(jsonpkg)
        return True
    except Exception, e:
	debug(str(signal)+str(e))
        return False

def getUndistriUser(drop_first=False):
    '''
    一定要返回未分配且有效user，测试不通过的直接丢掉
    '''
    if drop_first:
        undistri_queue.put_nowait(undistri_queue.get())
    while True:
        user = undistri_queue.get()
        if sendJSON(signal=TEST, recvcon=user[1]):
            return user

def chatRecv(user):
    '''
    多个gl，每个gl各负责从对应user接收消息并插入队列
    只判别是否切换聊天对象，无法判断是否断线，交给另外一个检测gl处理
    '''
    userid = user[0]
    usercon = user[1]
    while True:
        msgjs = usercon.recv(2048)
	if not msgjs:
	    return
	debug(msgjs)
        msg = json.loads(msgjs)
        if msg[0] == TEST:
            pass
        if msg[0] == CHECKOUT:
            # 切换只改变自身状态为未分配，不改变聊天对象状态
            if distri_dict.has_key(userid):
                user2id = distri_dict[userid][0] # 获取聊天对象的id
                # 如果聊天对象还在已分配群中，则通知其切换
                if distri_dict.has_key(user2id):
                    objid, usercon2, gl = distri_dict[user2id] # 获取聊天对象的socket
                    sendJSON(signal=MISS, recvcon=usercon2) # 发送MISS信号告知对象要切换
                del distri_dict[userid] # 把当前用户移出已分配群
                undistri_queue.put_nowait(user) # 把当前用户移回未分配队列
            return # gl任务结束
        if msg[0] == CHAT:
            msgQ.put_nowait([msg[2],msg[3],msgjs])
        gevent.sleep(0)

def chatSend():
    '''
    对消息队列的消息进行分发
    '''
    while True:
        msg = msgQ.get()
        sendid = msg[0]
        recvid = msg[1]
        msgjs = msg[2]
        # 分发消息时如果失败，则告知双方已断线
        # 发送信号MISS
        boolcon1 = sendJSON(signal=CHAT, toid=sendid, JSON=msgjs,)
        boolcon2 = sendJSON(signal=CHAT, toid=recvid, JSON=msgjs,)
        if boolcon1 and boolcon2:
            pass
        elif boolcon1 and not boolcon2:
            sendJSON(signal=MISS, recvid=sendid)
            pool.discard(distri_dict[recvid][2])
            del distri_dict[recvid]
        elif boolcon2 and not boolcon1:
            sendJSON(signal=MISS, recvid=recvid)
            pool.discard(distri_dict[sendid][2])
            del distri_dict[sendid]
        else:
            pool.discard(distri_dict[sendid][2])
            pool.discard(distri_dict[recvid][2])
            del distri_dict[sendid]
            del distri_dict[recvid]
        gevent.sleep(0)

def chatCheck():
    '''
    循环检测已配对用户连接，2秒发一次
    '''
    while True:
        debug('循环检测已配对池中的连接状态')
        for userid in distri_dict.keys():
            objid, usercon, gl = distri_dict[userid]
            # 如果双方均断线，就得释放资源了
            if not sendJSON(TEST, recvcon=usercon):
                if distri_dict.has_key(objid):
                    if not sendJSON(signal=MISS, toid=objid):
                        pool.discard(distri_dict[objid][2])
                        del distri_dict[objid]
                else:
                    pass
                pool.discard(gl)
                del distri_dict[userid]
        gevent.sleep(2)

def waitSoc():
    '''
    循环监听端口，如果有新链接则分配uuid，告知之，加入未分配队列
    '''
    while True:
        debug('开始监听端口')
        cliSoc, addr = listenSoc.accept()
        debug('生成uuid')
        generid = uuid.uuid4()
        debug('发送uuid告知用户')
        sendJSON(signal=INIT, msg=str(generid), recvcon=cliSoc)
        debug('加入待分配队列')
        undistri_queue.put_nowait([str(generid), cliSoc])
        gevent.sleep(0)

def distribute():
    '''
    配对
    '''
    while True:
        user1 = getUndistriUser(True)
        debug('获取第一个用户')
        user2 = getUndistriUser()
        debug('获取第二个用户')
        gl1, gl2 = None, None
        if not pool.full():
            gl1 = gevent.spawn(chatRecv, user1)
            pool.add(gl1)
            debug('将第一个用户加入并发池'+str(gl1.started))
        if not pool.full():
            gl2 = gevent.spawn(chatRecv, user2)
            pool.add(gl2)
            debug('将第二个用户加入并发池'+str(gl2.started))
        debug('并发池还有'+str(pool.free_count())+'空位')
        distri_dict[user1[0]] = [user2[0], user1[1], gl1]
        distri_dict[user2[0]] = [user1[0], user2[1], gl2]
        debug('将两个用户加入已配对队列')
        sendJSON(signal=DSTB, msg=user2[0], toid=user1[0])
        sendJSON(signal=DSTB, msg=user1[0], toid=user2[0])
        debug('发送告知两个用户DSTB')
        gevent.sleep(0)

def main():
    gevent.joinall([
            gevent.spawn(waitSoc),
            gevent.spawn(distribute),
            gevent.spawn(chatSend),
            # gevent.spawn(chatCheck),
            ])

if __name__ == '__main__':
    main()
