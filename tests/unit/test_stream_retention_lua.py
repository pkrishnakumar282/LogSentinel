"""Execute production watermark logic with Lua stubs, not a substitute for live Redis tests."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from shared.pipeline.redis_streams import TRIM


@unittest.skipUnless(shutil.which('lua'),'Local Lua interpreter unavailable')
class RetentionWatermarkTests(unittest.TestCase):
    def evaluate(self,groups,pending,expected):
        code='''
local groups=__GROUPS__
local pending=__PENDING__
local cutoff=nil
redis={call=function(command,...)
 local args={...}
 if command=='XINFO' then return groups end
 if command=='XPENDING' then return pending[args[2]] or {} end
 if command=='XTRIM' then cutoff=args[4]; return 42 end
 error('unexpected command')
end}
KEYS={'stream'}
local operation=assert(load([==[SCRIPT]==]))
operation()
assert(cutoff=='EXPECTED','unsafe retention cutoff: '..tostring(cutoff))
'''.replace('__GROUPS__',groups).replace('__PENDING__',pending).replace('SCRIPT',TRIM).replace('EXPECTED',expected)
        result=subprocess.run(['lua','-'],input=code,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_unread_group_and_old_pending_entries_block_trimming(self):
        self.evaluate("{{'name','a','last-delivered-id','100-0'},{'name','b','last-delivered-id','0-0'}}",
                      "{a={{'20-0','consumer',0,1}}}",'0-0')
        self.evaluate("{{'name','a','last-delivered-id','100-0'},{'name','b','last-delivered-id','80-0'}}",
                      "{a={{'20-0','consumer',0,1}}}",'20-0')

    def test_stream_ids_are_compared_without_floating_point_loss(self):
        self.evaluate("{{'name','a','last-delivered-id','9007199254740993-0'},{'name','b','last-delivered-id','9007199254740992-0'}}",
                      '{}','9007199254740992-0')
        self.evaluate("{{'name','a','last-delivered-id','10-9007199254740993'},{'name','b','last-delivered-id','10-9007199254740992'}}",
                      '{}','10-9007199254740992')


if __name__=='__main__':unittest.main()
