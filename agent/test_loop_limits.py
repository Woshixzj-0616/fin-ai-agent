"""真实模型测试发现的工具限额与检查点记账回归。"""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from audit.loop import run_loop, run_loop_native
from audit.schema import LLMError
from backend_runtime import ExecutionBudget, load_config
from materials import Run

def native_calls(names):
    return {"role":"assistant","content":None,"tool_calls":[
        {"id":f"c{i}","type":"function","function":{"name":n,
         "arguments":json.dumps({"items":[],"unclaimed_sentences":[1]} if n=="submit_claims" else {"kind":"metrics"})}}
        for i,n in enumerate(names)]}

class Client:
    def __init__(self, frames, config, run):
        self.frames=list(frames);self.config=config;self.calls=0
        self._execution=ExecutionBudget(config,run)
        self.saved=[]
        self._checkpoint_commit=lambda:self.saved.append(self._execution.snapshot())
    def chat_tools(self,messages,tools,run):
        self.calls+=1;return self.frames.pop(0)
    def chat(self,messages,run):
        self.calls+=1;return json.dumps(self.frames.pop(0))

class LoopLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="fin_loop_limits_")
        self.addCleanup(self.temp.cleanup)
        self.run=Run(Path(self.temp.name),"loop_limits",{})
        self.config=load_config()
    def invoke(self,native,client):
        return (run_loop_native if native else run_loop)(client,"测试",[],self.run,
               sentences=[{"sentence_id":1,"text":"测试"}])
    def frames(self,native):
        if native:return [native_calls(["list_catalog","list_catalog"]),native_calls(["submit_claims"])]
        return [{"action":"call_tools","tool_calls":[{"name":"list_catalog","arguments":{"kind":"metrics"}}]*2},
                {"action":"submit_claims","items":[],"unclaimed_sentences":[1]}]
    def test_configured_calls_and_checkpoint_counter_native(self): self.check_calls(True)
    def test_configured_calls_and_checkpoint_counter_json(self): self.check_calls(False)
    def check_calls(self,native):
        self.config["tools"]["max_calls"]=1
        client=Client(self.frames(native),self.config,self.run)
        result=self.invoke(native,client)
        self.assertEqual(len(result["tools_used"]),1)
        self.assertEqual(client._execution.snapshot()["tool_calls"],1)
        self.assertEqual(client.saved[-1]["tool_calls"],1)
        completions=[json.loads(e) for e in self.run.events if json.loads(e)["event"]=="agent_tool"]
        self.assertEqual(completions[-1]["status"],"budget_exhausted")
    def test_restored_budget_is_shared_with_next_loop_native(self): self.check_shared(True)
    def test_restored_budget_is_shared_with_next_loop_json(self): self.check_shared(False)
    def check_shared(self,native):
        self.config["tools"]["max_calls"]=1
        client=Client(self.frames(native),self.config,self.run)
        client._execution.before_tool()
        client._execution=ExecutionBudget(self.config,self.run,client._execution.snapshot())
        result=self.invoke(native,client)
        self.assertEqual(result["tools_used"],[])
        self.assertEqual(client._execution.tool_calls,1)
    def test_configured_round_limit_native(self): self.check_rounds(True)
    def test_configured_round_limit_json(self): self.check_rounds(False)
    def check_rounds(self,native):
        self.config["tools"]["max_rounds"]=1
        client=Client(self.frames(native),self.config,self.run)
        with self.assertRaises(LLMError): self.invoke(native,client)
        self.assertEqual(client.calls,1)
    def test_no_submit_repairs_when_disabled_native(self): self.check_repairs(True)
    def test_no_submit_repairs_when_disabled_json(self): self.check_repairs(False)
    def check_repairs(self,native):
        self.config["tools"]["max_repairs"]=0
        malformed={"items":[{}],"unclaimed_sentences":[]}
        if native:
            frame=native_calls(["submit_claims"])
            frame["tool_calls"][0]["function"]["arguments"]=json.dumps(malformed)
        else: frame={"action":"submit_claims",**malformed}
        client=Client([frame],self.config,self.run)
        with self.assertRaises(LLMError):self.invoke(native,client)
        self.assertEqual(client.calls,1)

if __name__=="__main__": unittest.main()
