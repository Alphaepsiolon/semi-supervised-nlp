import pandas as pd
import numpy as np
import torch
import torch.nn.functional as F
import argparse
from transformers import *
from sklearn.metrics import f1_score
import random
import time
from lib.utils.utils import ratio_undersample, dataset_adaptor, format_time
from lib.dataset.dataloader import generate_data_loader
from lib.models.ganbert import *


#--------------------------------
#  Transformer parameters
#--------------------------------
max_seq_length = 64
batch_size = 64

#--------------------------------
#  GAN-BERT specific parameters
#--------------------------------
# number of hidden layers in the generator, 
# each of the size of the output space
num_hidden_layers_g = 1; 
# number of hidden layers in the discriminator, 
# each of the size of the input space
num_hidden_layers_d = 1; 
# size of the generator's input noisy vectors
noise_size = 100
# dropout to be applied to discriminator's input vectors
out_dropout_rate = 0.2

# Replicate labeled data to balance poorly represented datasets, 
# e.g., less than 1% of labeled material
apply_balance = True

#--------------------------------
#  Optimization parameters
#--------------------------------
learning_rate_discriminator = 5e-5
learning_rate_generator = 5e-5
epsilon = 1e-8
num_train_epochs = 50
multi_gpu = True
# Scheduler
apply_scheduler = False
warmup_proportion = 0.1
# Print
print_each_n_step = 10


if __name__ == "__main__":
    seed_val = 42
    random.seed(seed_val)
    np.random.seed(seed_val)
    torch.manual_seed(seed_val)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed_val)

    # If there's a GPU available...
    if torch.cuda.is_available():    
        # Tell PyTorch to use the GPU.    
        device = torch.device("cuda")
        print('There are %d GPU(s) available.' % torch.cuda.device_count())
        print('We will use the GPU:', torch.cuda.get_device_name(0))
    # If not...
    else:
        print('No GPU available, using the CPU instead.')
        device = torch.device("cpu")
    model_name = 'albert-base-v2'
    atis_train = pd.read_csv('/home/ubuntu/adithya/temp/EAMLA/NLP2/semi-supervised-nlp/lib/data/atis_intents_train.csv', names=['label','text'])
    
    # take only 10% of the total dataset
    atis_train_10 = ratio_undersample(atis_train, 0.1)
    atis_test = pd.read_csv('/home/ubuntu/adithya/temp/EAMLA/NLP2/semi-supervised-nlp/lib/data/atis_intents_test.csv',names=['label','text'])
    

    # get model and tokenizer
    transformer = AutoModel.from_pretrained(model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name)  

    # get unique labels
    label_list = atis_train.label.unique().tolist()
    labeled_examples = dataset_adaptor(atis_train_10)
    unlabeled_examples = test_examples = dataset_adaptor(atis_test)

    label_map = {}
    for (i, label) in enumerate(label_list):
        label_map[label] = i
    #------------------------------
    #   Load the train dataset
    #------------------------------
    train_examples = labeled_examples
    #The labeled (train) dataset is assigned with a mask set to True
    train_label_masks = np.ones(len(labeled_examples), dtype=bool)
    #If unlabel examples are available
    if unlabeled_examples:
        train_examples = train_examples + unlabeled_examples
        #The unlabeled (train) dataset is assigned with a mask set to False
        tmp_masks = np.zeros(len(unlabeled_examples), dtype=bool)
        train_label_masks = np.concatenate([train_label_masks,tmp_masks])

    train_dataloader = generate_data_loader(train_examples, train_label_masks, label_map, batch_size=batch_size, max_seq_length=max_seq_length, tokenizer=tokenizer, do_shuffle = True, balance_label_examples = apply_balance)

    #------------------------------
    #   Load the test dataset
    #------------------------------
    #The labeled (test) dataset is assigned with a mask set to True
    test_label_masks = np.ones(len(test_examples), dtype=bool)

    test_dataloader = generate_data_loader(test_examples, test_label_masks, label_map, batch_size=batch_size, max_seq_length=max_seq_length, tokenizer=tokenizer, do_shuffle = False, balance_label_examples = False)

    

    # The config file is required to get the dimension of the vector produced by 
    # the underlying transformer
    config = AutoConfig.from_pretrained(model_name)
    hidden_size = int(config.hidden_size)
    # Define the number and width of hidden layers
    hidden_levels_g = [hidden_size for i in range(0, num_hidden_layers_g)]
    hidden_levels_d = [hidden_size for i in range(0, num_hidden_layers_d)]

    #-------------------------------------------------
    #   Instantiate the Generator and Discriminator
    #-------------------------------------------------
    generator = Generator(noise_size=noise_size, output_size=hidden_size, hidden_sizes=hidden_levels_g, dropout_rate=out_dropout_rate)
    discriminator = Discriminator(input_size=hidden_size, hidden_sizes=hidden_levels_d, num_labels=len(label_list), dropout_rate=out_dropout_rate)

    # Put everything in the GPU if available
    if torch.cuda.is_available():    
        generator.cuda()
        discriminator.cuda()
        transformer.cuda()
        if multi_gpu:
            transformer = torch.nn.DataParallel(transformer)

    # print(config)
    
    training_stats = []

    # Measure the total training time for the whole run.
    total_t0 = time.time()

    #models parameters
    transformer_vars = [i for i in transformer.parameters()]
    d_vars = transformer_vars + [v for v in discriminator.parameters()]
    g_vars = [v for v in generator.parameters()]

    #optimizer
    dis_optimizer = torch.optim.AdamW(d_vars, lr=learning_rate_discriminator)
    gen_optimizer = torch.optim.AdamW(g_vars, lr=learning_rate_generator) 

    #scheduler
    if apply_scheduler:
        num_train_examples = len(train_examples)
        num_train_steps = int(num_train_examples / batch_size * num_train_epochs)
        num_warmup_steps = int(num_train_steps * warmup_proportion)

        scheduler_d = get_constant_schedule_with_warmup(dis_optimizer, 
                                                num_warmup_steps = num_warmup_steps)
        scheduler_g = get_constant_schedule_with_warmup(gen_optimizer, 
                                            num_warmup_steps = num_warmup_steps)

    # For each epoch...
    for epoch_i in range(0, num_train_epochs):
        # ========================================
        #               Training
        # ========================================
        # Perform one full pass over the training set.
        print("")
        print('======== Epoch {:} / {:} ========'.format(epoch_i + 1, num_train_epochs))
        print('Training...')

        # Measure how long the training epoch takes.
        t0 = time.time()

        # Reset the total loss for this epoch.
        tr_g_loss = 0
        tr_d_loss = 0

        # Put the model into training mode.
        transformer.train() 
        generator.train()
        discriminator.train()

        # For each batch of training data...
        for step, batch in enumerate(train_dataloader):

            # Progress update every print_each_n_step batches.
            if step % print_each_n_step == 0 and not step == 0:
                # Calculate elapsed time in minutes.
                elapsed = format_time(time.time() - t0)
                
                # Report progress.
                print('  Batch {:>5,}  of  {:>5,}.    Elapsed: {:}.'.format(step, len(train_dataloader), elapsed))

            # Unpack this training batch from our dataloader. 
            b_input_ids = batch[0].to(device)
            b_input_mask = batch[1].to(device)
            b_labels = batch[2].to(device)
            b_label_mask = batch[3].to(device)

            real_batch_size = b_input_ids.shape[0]
        
            # Encode real data in the Transformer
            model_outputs = transformer(b_input_ids, attention_mask=b_input_mask)
            hidden_states = model_outputs[-1]
            
            # Generate fake data that should have the same distribution of the ones
            # encoded by the transformer. 
            # First noisy input are used in input to the Generator
            noise = torch.zeros(real_batch_size, noise_size, device=device).uniform_(0, 1)
            # Gnerate Fake data
            gen_rep = generator(noise)

            # Generate the output of the Discriminator for real and fake data.
            # First, we put together the output of the tranformer and the generator
            disciminator_input = torch.cat([hidden_states, gen_rep], dim=0)
            # Then, we select the output of the disciminator
            features, logits, probs = discriminator(disciminator_input)

            # Finally, we separate the discriminator's output for the real and fake
            # data
            features_list = torch.split(features, real_batch_size)
            D_real_features = features_list[0]
            D_fake_features = features_list[1]
        
            logits_list = torch.split(logits, real_batch_size)
            D_real_logits = logits_list[0]
            D_fake_logits = logits_list[1]
            
            probs_list = torch.split(probs, real_batch_size)
            D_real_probs = probs_list[0]
            D_fake_probs = probs_list[1]

            #---------------------------------
            #  LOSS evaluation
            #---------------------------------
            # Generator's LOSS estimation
            g_loss_d = -1 * torch.mean(torch.log(1 - D_fake_probs[:,-1] + epsilon))
            g_feat_reg = torch.mean(torch.pow(torch.mean(D_real_features, dim=0) - torch.mean(D_fake_features, dim=0), 2))
            g_loss = g_loss_d + g_feat_reg
    
            # Disciminator's LOSS estimation
            logits = D_real_logits[:,0:-1]
            log_probs = F.log_softmax(logits, dim=-1)
            # The discriminator provides an output for labeled and unlabeled real data
            # so the loss evaluated for unlabeled data is ignored (masked)
            label2one_hot = torch.nn.functional.one_hot(b_labels, len(label_list))
            per_example_loss = -torch.sum(label2one_hot * log_probs, dim=-1)
            per_example_loss = torch.masked_select(per_example_loss, b_label_mask.to(device))
            labeled_example_count = per_example_loss.type(torch.float32).numel()

            # It may be the case that a batch does not contain labeled examples, 
            # so the "supervised loss" in this case is not evaluated
            if labeled_example_count == 0:
                D_L_Supervised = 0
            else:
                D_L_Supervised = torch.div(torch.sum(per_example_loss.to(device)), labeled_example_count)
                    
            D_L_unsupervised1U = -1 * torch.mean(torch.log(1 - D_real_probs[:, -1] + epsilon))
            D_L_unsupervised2U = -1 * torch.mean(torch.log(D_fake_probs[:, -1] + epsilon))
            d_loss = D_L_Supervised + D_L_unsupervised1U + D_L_unsupervised2U

            #---------------------------------
            #  OPTIMIZATION
            #---------------------------------
            # Avoid gradient accumulation
            gen_optimizer.zero_grad()
            dis_optimizer.zero_grad()

            # Calculate weigth updates
            # retain_graph=True is required since the underlying graph will be deleted after backward
            g_loss.backward(retain_graph=True)
            d_loss.backward() 
            
            # Apply modifications
            gen_optimizer.step()
            dis_optimizer.step()

            # A detail log of the individual losses
            #print("{0:.4f}\t{1:.4f}\t{2:.4f}\t{3:.4f}\t{4:.4f}".
            #      format(D_L_Supervised, D_L_unsupervised1U, D_L_unsupervised2U,
            #             g_loss_d, g_feat_reg))

            # Save the losses to print them later
            tr_g_loss += g_loss.item()
            tr_d_loss += d_loss.item()

            # Update the learning rate with the scheduler
            if apply_scheduler:
                scheduler_d.step()
                scheduler_g.step()

        # Calculate the average loss over all of the batches.
        avg_train_loss_g = tr_g_loss / len(train_dataloader)
        avg_train_loss_d = tr_d_loss / len(train_dataloader)             
        
        # Measure how long this epoch took.
        training_time = format_time(time.time() - t0)

        print("")
        print("  Average training loss generetor: {0:.3f}".format(avg_train_loss_g))
        print("  Average training loss discriminator: {0:.3f}".format(avg_train_loss_d))
        print("  Training epcoh took: {:}".format(training_time))
            
        # ========================================
        #     TEST ON THE EVALUATION DATASET
        # ========================================
        # After the completion of each training epoch, measure our performance on
        # our test set.
        print("")
        print("Running Test...")

        t0 = time.time()

        # Put the model in evaluation mode--the dropout layers behave differently
        # during evaluation.
        transformer.eval() #maybe redundant
        discriminator.eval()
        generator.eval()

        # Tracking variables 
        total_test_accuracy = 0
    
        total_test_loss = 0
        nb_test_steps = 0

        all_preds = []
        all_labels_ids = []

        #loss
        nll_loss = torch.nn.CrossEntropyLoss(ignore_index=-1)

        # Evaluate data for one epoch
        for batch in test_dataloader:
            
            # Unpack this training batch from our dataloader. 
            b_input_ids = batch[0].to(device)
            b_input_mask = batch[1].to(device)
            b_labels = batch[2].to(device)
            
            # Tell pytorch not to bother with constructing the compute graph during
            # the forward pass, since this is only needed for backprop (training).
            with torch.no_grad():        
                model_outputs = transformer(b_input_ids, attention_mask=b_input_mask)
                hidden_states = model_outputs[-1]
                _, logits, probs = discriminator(hidden_states)
                ###log_probs = F.log_softmax(probs[:,1:], dim=-1)
                filtered_logits = logits[:,0:-1]
                # Accumulate the test loss.
                total_test_loss += nll_loss(filtered_logits, b_labels)
                
            # Accumulate the predictions and the input labels
            _, preds = torch.max(filtered_logits, 1)
            all_preds += preds.detach().cpu()
            all_labels_ids += b_labels.detach().cpu()

        # Report the final accuracy for this validation run.
        all_preds = torch.stack(all_preds).numpy()
        all_labels_ids = torch.stack(all_labels_ids).numpy()
        test_accuracy = np.sum(all_preds == all_labels_ids) / len(all_preds)
        test_f1_score_micro = f1_score(all_preds, all_labels_ids, average='micro')
        test_f1_score_macro = f1_score(all_preds, all_labels_ids, average='macro')
        print("  Accuracy: {0:.3f}".format(test_accuracy))
        print(f" f1 micro: {test_f1_score_micro}")
        print(f" f1 macro: {test_f1_score_macro}")

        # Calculate the average loss over all of the batches.
        avg_test_loss = total_test_loss / len(test_dataloader)
        avg_test_loss = avg_test_loss.item()
        
        # Measure how long the validation run took.
        test_time = format_time(time.time() - t0)
        
        print("  Test Loss: {0:.3f}".format(avg_test_loss))
        print("  Test took: {:}".format(test_time))

        # Record all statistics from this epoch.
        training_stats.append(
            {
                'epoch': epoch_i + 1,
                'Training Loss generator': avg_train_loss_g,
                'Training Loss discriminator': avg_train_loss_d,
                'Valid. Loss': avg_test_loss,
                'Valid. Accur.': test_accuracy,
                'Training Time': training_time,
                'Test Time': test_time,
                'Test accuracy' : test_accuracy,
                'Test f1_score_macro': test_f1_score_macro,
                'Test f1_score_micro' : test_f1_score_micro
            }
        )

    # generate a csv
    training_stats_df = pd.DataFrame(training_stats)
    training_stats_df.to_csv('/home/ubuntu/adithya/temp/EAMLA/NLP2/semi-supervised-nlp/results/results_albert.csv')