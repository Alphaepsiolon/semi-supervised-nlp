# semi-supervised-nlp
A repo to rest out some semi supervised training approaches for text classification in NLP.

## Dataset
The dataset I'll be using will be the ATIS Intent Classification dataset. You can find the details and 
the link here: (ATIS Dataset)[https://www.kaggle.com/datasets/hassanamin/atis-airlinetravelinformationsystem].

## Approach
We'll be testing out a generative approach for semi-supervised. Specifically, we'll be testing out the approach mentioned
here: (GAN-BERT)[https://github.com/crux82/ganbert].

A neat thing about this, and subsequently it's PyTorch implementation is that it's compatible with huggingface. However the PyTorch implementation (here)[https://github.com/crux82/ganbert-pytorch] lacks a wrapping framework to iterate on. Hence this repository will implement the convenience functions.

Details regarding the model architecture can be found either on the original paper or the links above.

## Installation
```
conda create -n ssn python=3.8
pip install -r requirements.txt
```

## Running Training
```
python trainer.py
```
All results (training, val and test) results are saved to results.csv per epoch. From this, we select the epoch weights with the best overall val-set scores.