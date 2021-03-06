//===- RealmABI.cpp - Lower Tapir into Realm runtime system calls -----------===//
//
//                     The LLVM Compiler Infrastructure
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//
//
// This file implements the RealmABI interface, which is used to convert Tapir
// instructions -- detach, reattach, and sync -- to calls into the Realm
// runtime system.  
//
//===----------------------------------------------------------------------===//

//Need to add some way to include the kitsune-rt stuff I wrote

#include "llvm/Transforms/Tapir/RealmABI.h"
#include "llvm/ADT/Statistic.h"
#include "llvm/Analysis/AssumptionCache.h"
#include "llvm/Analysis/LoopInfo.h"
#include "llvm/Analysis/LoopIterator.h"
#include "llvm/Analysis/OptimizationRemarkEmitter.h"
#include "llvm/Analysis/ScalarEvolution.h"
#include "llvm/Analysis/ScalarEvolutionExpander.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InlineAsm.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/TypeBuilder.h"
#include "llvm/IR/Verifier.h"
#include "llvm/Transforms/Tapir/Outline.h"
#include "llvm/Transforms/Utils/BasicBlockUtils.h"
#include "llvm/Transforms/Utils/EscapeEnumerator.h"
#include "llvm/Transforms/Utils/Local.h"
#include "llvm/Transforms/Utils/TapirUtils.h"
#include "llvm/Transforms/Utils/ValueMapper.h"

using namespace llvm;

#define DEBUG_TYPE "realmabi"

//can I replace the following by just using the realm_task_pointer_t type in realm_c.h?
typedef void (*TaskFuncPtr)(const void *args, size_t arglen,
			   const void *user_data, size_t user_data_len,
			   unsigned long long proc);

//typedefs used as part of the auto-function generation for calls to Realm
typedef int (realmInitRuntime_t)(int argc, char** argv); 
typedef int (realmSync_t)();
typedef int (realmSpawn_t)(TaskFuncPtr fxn, const void *args, size_t arglen,
			   void *user_data, size_t user_data_len);

#define REALM_FUNC(name, CGF) get_##name(CGF)

#define DEFAULT_GET_REALM_FUNC(name)                                  \
  static Function *get_##name(Module& M) {         \
    return cast<Function>(M.getOrInsertFunction(            \
                                          #name,            \
                                          TypeBuilder< name ## _t , false >::get(M.getContext()) \
							)); \
  }

DEFAULT_GET_REALM_FUNC(realmSpawn)
DEFAULT_GET_REALM_FUNC(realmSync)
DEFAULT_GET_REALM_FUNC(realmInitRuntime)


Value *RealmABI::GetOrCreateWorker8(Function &F) {
  LLVMContext& C = F.getParent()->getContext(); 
  return ConstantInt::get(C, APInt(16, 8)); //Note: stole this from PTXABI.cpp
}

void RealmABI::createSync(SyncInst &SI, ValueToValueMapTy &DetachCtxToStackFrame) {
  IRBuilder<> builder(&SI); 
  auto F = SI.getParent()->getParent(); 
  auto M = F->getParent();

  CallInst::Create(REALM_FUNC(realmSync, *M), "", F->getEntryBlock().getFirstNonPHIOrDbg());

  BranchInst *PostSync = BranchInst::Create(SI.getSuccessor(0));
  ReplaceInstWithInst(&SI, PostSync);
  return;
}

// Adds entry basic blocks to body of extracted, replacing extracted, and adds
// necessary code to call, i.e. storing arguments in struct
Function* formatFunctionToRealmF(Function* extracted, Instruction* ical){
  std::vector<Value*> LoadedCapturedArgs;
  CallInst *cal = dyn_cast<CallInst>(ical);

  for(auto& a:cal->arg_operands()) {
    LoadedCapturedArgs.push_back(a);
  }

  Module *M = extracted->getParent(); 
  auto& C = M->getContext(); 
  DataLayout DL(M);
  IRBuilder<> CallerIRBuilder(cal);

  //get the argument types
  auto FnParams = extracted->getFunctionType()->params();
  StructType *ArgsTy = StructType::create(FnParams, "anon");
  auto *ArgsPtrTy = PointerType::getUnqual(ArgsTy);

  //Create the canonical TaskFuncPtr
  ArrayRef<Type*> typeArray = {Type::getInt8PtrTy(C), Type::getInt64Ty(C), Type::getInt8PtrTy(C), Type::getInt64Ty(C), Type::getInt64Ty(C)}; //trying int64 as stand-in for Realm::Processor because a ::realm_id_t is ultimately an unsigned long long

  FunctionType *OutlinedFnTy = FunctionType::get(
      Type::getInt8Ty(C), 
      typeArray,
      false);

  Function *OutlinedFn = Function::Create(
      OutlinedFnTy, GlobalValue::InternalLinkage, ".realm_outlined.", M);
  OutlinedFn->addFnAttr(Attribute::AlwaysInline);
  OutlinedFn->addFnAttr(Attribute::NoUnwind);
  OutlinedFn->addFnAttr(Attribute::UWTable);

  //StringRef ArgNames[] = {".args"};
  std::vector<Value*> out_args;
  for (auto &Arg : OutlinedFn->args()) {
    //Arg.setName(ArgNames[out_args.size()]);
    Arg.setName("");
    out_args.push_back(&Arg);
  }

  // Entry Code
  auto *EntryBB = BasicBlock::Create(C, "entry", OutlinedFn, nullptr);
  IRBuilder<> EntryBuilder(EntryBB);
  auto argStructPtr = EntryBuilder.CreateBitCast(out_args[0], ArgsPtrTy); 
  ValueToValueMapTy valmap;

  unsigned int argc = 0;
  for (auto& arg : extracted->args()) {
    auto *DataAddrEP = EntryBuilder.CreateStructGEP(ArgsTy, argStructPtr, argc); 
    auto *DataAddr = EntryBuilder.CreateAlignedLoad(
        DataAddrEP,
        DL.getTypeAllocSize(DataAddrEP->getType()->getPointerElementType()));
    valmap.insert(std::pair<Value*,Value*>(&arg,DataAddr));
    argc++;
  }

  // Replace return values with return zero 
  SmallVector< ReturnInst *,5> retinsts;
  CloneFunctionInto(OutlinedFn, extracted, valmap, true, retinsts);
  EntryBuilder.CreateBr(OutlinedFn->getBasicBlockList().getNextNode(*EntryBB));

  for (auto& ret : retinsts) {
    auto retzero = ReturnInst::Create(C, ConstantInt::get(Type::getInt8Ty(C), 0));
    ReplaceInstWithInst(ret, retzero);
  }

  // Caller code
  auto callerArgStruct = CallerIRBuilder.CreateAlloca(ArgsTy); 

  unsigned int cArgc = 0;
  for (auto& arg : LoadedCapturedArgs) {
    auto *DataAddrEP2 = CallerIRBuilder.CreateStructGEP(ArgsTy, callerArgStruct, cArgc); 
    CallerIRBuilder.CreateAlignedStore(
        LoadedCapturedArgs[cArgc], DataAddrEP2,
        DL.getTypeAllocSize(arg->getType()));
    cArgc++;
  }

  assert(argc == cArgc && "Wrong number of arguments passed to outlined function"); 

  auto outlinedFnPtr = CallerIRBuilder.CreatePointerBitCastOrAddrSpaceCast(
									   OutlinedFn, TypeBuilder<TaskFuncPtr, false>::get(M->getContext())); 
  auto argSize = ConstantInt::get(Type::getInt64Ty(C), ArgsTy->getNumElements()); 
  auto argDataSize = ConstantInt::get(Type::getInt64Ty(C), DL.getTypeAllocSize(ArgsTy)); 
  auto argsStructVoidPtr = CallerIRBuilder.CreateBitCast(callerArgStruct, Type::getInt8PtrTy(C)); 

  //std::vector<Value *> callerArgs = { outlinedFnPtr, argsStructVoidPtr, argSize, argsStructVoidPtr, argDataSize}; 

  ArrayRef<Value *> callerArgs = { outlinedFnPtr, argsStructVoidPtr, argSize, argsStructVoidPtr, argDataSize}; 

  CallerIRBuilder.CreateCall(REALM_FUNC(realmSpawn, *M), callerArgs); 

  cal->eraseFromParent();
  extracted->eraseFromParent();

  LLVM_DEBUG(OutlinedFn->dump()); 

  return OutlinedFn; 
}

Function *RealmABI::createDetach(DetachInst &detach,
				 ValueToValueMapTy &DetachCtxToStackFrame,
				 DominatorTree &DT, AssumptionCache &AC) {
  BasicBlock *detB = detach.getParent();
  Function &F = *(detB->getParent());
  BasicBlock *Spawned  = detach.getDetached();
  BasicBlock *Continue = detach.getContinue();

  Instruction *cal = nullptr;
  Function *extracted = extractDetachBodyToFunction(detach, DT, AC, &cal);
  extracted = formatFunctionToRealmF(extracted, cal); 

  // Replace the detach with a branch to the continuation.
  BranchInst *ContinueBr = BranchInst::Create(Continue);
  ReplaceInstWithInst(&detach, ContinueBr);

  // Rewrite phis in the detached block.
  {
    BasicBlock::iterator BI = Spawned->begin();
    while (PHINode *P = dyn_cast<PHINode>(BI)) {
      P->removeIncomingValue(detB);
      ++BI;
    }
  }

  LLVM_DEBUG(F.dump()); 

  return extracted;
}

void RealmABI::preProcessFunction(Function &F) {}

void RealmABI::postProcessFunction(Function &F) {
  Module *M = F.getParent();
  LLVMContext &C = M->getContext();
  IRBuilder<> builder(F.getEntryBlock().getFirstNonPHIOrDbg());

  //default values of 0 and nullptr
  //TODO: handle the case where main actually has an argc and argv
  Value* zero = ConstantInt::get(Type::getInt32Ty(C), 0);
  Value* null = Constant::getNullValue(PointerType::getUnqual(Type::getInt8PtrTy(C))); //TODO: make a char**?
  ArrayRef<Value*> initArgs = {zero, null};

  builder.CreateCall(REALM_FUNC(realmInitRuntime, *M), initArgs);
}

void RealmABI::postProcessHelper(Function &F) {}

