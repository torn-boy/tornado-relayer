// Executed inside the relayer image on the smoke test's private Docker network.
const http = require('node:http')
const WebSocket = require('ws')
const { ethers } = require('ethers')
const MerkleTree = require('@tornado/fixed-merkle-tree')
const { getMultiCallContract } = require('@tornado/tornado-oracles/lib/contracts/factories')
const { poseidonHash2, toBN } = require('./src/utils')
const events = require('./cache/accounts_farmer_1.json')

const head = events[events.length - 1].blockNumber
const tree = new MerkleTree(20, events.map(event => toBN(event.commitment)), { hashFunction: poseidonHash2 })
const hex = ethers.utils.hexValue
const accountRoot = ethers.utils.hexZeroPad(hex(ethers.BigNumber.from(tree.root().toString())), 32)
const address = '0x0000000000000000000000000000000000000001'
const aggregator = new ethers.utils.Interface(require('./abis/Aggregator.abi.json'))
const miner = new ethers.utils.Interface(require('./abis/mining.abi.json'))
const multicall = getMultiCallContract(new ethers.providers.JsonRpcProvider()).interface
const tokenRate = ethers.utils.defaultAbiCoder.encode(['uint256'], [ethers.utils.parseEther('1')])
let nextSubscription = 0

function contractCall(data) {
  if (data.startsWith(aggregator.getSighash('bulkResolve'))) {
    const [ids] = aggregator.decodeFunctionData('bulkResolve', data)
    return aggregator.encodeFunctionResult('bulkResolve', [ids.map(() => address)])
  }
  if (data.startsWith(miner.getSighash('getLastAccountRoot'))) {
    return miner.encodeFunctionResult('getLastAccountRoot', [accountRoot])
  }
  if (data.startsWith(multicall.getSighash('multicall'))) {
    const [calls] = multicall.decodeFunctionData('multicall', data)
    const results = multicall.getFunction('multicall').outputs.map(output =>
      calls.map(() => output.name === 'success' ? true : tokenRate)
    )
    return multicall.encodeFunctionResult('multicall', results)
  }
  throw new Error('Unsupported fixture contract call')
}

function rpcResult(method, params) {
  switch (method) {
    case 'eth_chainId': return '0x1'
    case 'net_version': return '1'
    case 'eth_blockNumber': return hex(head)
    case 'eth_getBalance': return hex(ethers.utils.parseEther('1'))
    case 'eth_getTransactionCount': return '0x0'
    case 'eth_gasPrice': return hex(ethers.utils.parseUnits('1', 'gwei'))
    case 'eth_getCode': return '0x'
    case 'eth_getLogs': return []
    case 'eth_subscribe': return hex(++nextSubscription)
    case 'eth_unsubscribe': return true
    case 'eth_call': return contractCall(params[0].data)
    default: throw new Error('Unsupported fixture method ' + method)
  }
}

function reply(request) {
  if (Array.isArray(request)) return request.map(reply)
  const { id, method, params } = request
  try {
    return { jsonrpc: '2.0', id, result: rpcResult(method, params) }
  } catch (error) {
    return { jsonrpc: '2.0', id, error: { code: -32601, message: error.message } }
  }
}

const server = http.createServer((req, res) => {
  const chunks = []
  req.on('data', chunk => chunks.push(chunk))
  req.on('end', () => {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify(reply(JSON.parse(Buffer.concat(chunks).toString()))))
  })
})
const sockets = new WebSocket.Server({ server })
sockets.on('connection', socket => {
  socket.on('message', message => socket.send(JSON.stringify(reply(JSON.parse(message.toString())))))
})
server.listen(8545, '0.0.0.0', () => console.log('Test RPC listening at block', head))
